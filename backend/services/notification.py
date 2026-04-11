# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""
Oeil — Notification Service
Email (SMTP), HTTP Webhook, MQTT alerts
"""
from __future__ import annotations

import asyncio
import json
import logging
import smtplib
import ssl
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import aiohttp

logger = logging.getLogger("oeil.notification")


class NotificationService:
    def __init__(self, settings):
        self.settings = settings

    async def send_alert(self, title: str, body: str, camera_id: str = ""):
        """Send alert via all configured channels (fire-and-forget)."""
        tasks = []
        if self.settings.OW_SMTP_HOST and self.settings.OW_ALERT_EMAIL:
            tasks.append(asyncio.create_task(self._send_email(title, body)))
        if self.settings.OW_WEBHOOK_URL:
            tasks.append(asyncio.create_task(self._send_webhook(title, body, camera_id)))
        if self.settings.OW_MQTT_URL:
            tasks.append(asyncio.create_task(self._send_mqtt(title, body, camera_id)))
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    logger.debug(f"Notification error: {r}")

    async def _send_email(self, subject: str, body: str):
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[Oeil] {subject}"
            msg["From"] = self.settings.OW_SMTP_USER
            msg["To"] = self.settings.OW_ALERT_EMAIL
            msg.attach(MIMEText(body, "plain"))
            html = f"""<html><body>
            <div style="font-family:sans-serif;max-width:500px">
            <h2 style="color:#e53935">🔴 Oeil Alert</h2>
            <h3>{subject}</h3>
            <p>{body}</p>
            <hr><small style="color:#999">Oeil — {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</small>
            </div></body></html>"""
            msg.attach(MIMEText(html, "html"))
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._smtp_send, msg)
            logger.info(f"Email sent: {subject}")
        except Exception as e:
            logger.error(f"Email failed: {e}")

    def _smtp_send(self, msg: MIMEMultipart):
        ctx = ssl.create_default_context()
        with smtplib.SMTP(self.settings.OW_SMTP_HOST, self.settings.OW_SMTP_PORT) as srv:
            srv.ehlo()
            srv.starttls(context=ctx)
            srv.login(self.settings.OW_SMTP_USER, self.settings.OW_SMTP_PASS)
            srv.send_message(msg)

    async def _send_webhook(self, title: str, body: str, camera_id: str):
        try:
            payload = {
                "title": title,
                "body": body,
                "camera_id": camera_id,
                "timestamp": datetime.utcnow().isoformat(),
                "source": "oeil",
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.settings.OW_WEBHOOK_URL,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    logger.info(f"Webhook sent: HTTP {resp.status}")
        except Exception as e:
            logger.error(f"Webhook failed: {e}")

    async def _send_mqtt(self, title: str, body: str, camera_id: str):
        """
        Basic MQTT publish via raw TCP (MQTT 3.1.1 PUBLISH packet).
        For production use, add paho-mqtt to requirements.
        """
        try:
            url = self.settings.OW_MQTT_URL  # mqtt://host:1883
            host = url.replace("mqtt://", "").split(":")[0]
            port = int(url.split(":")[-1]) if ":" in url.replace("mqtt://", "") else 1883
            topic = b"oeil/alerts"
            payload = json.dumps({
                "title": title, "body": body,
                "camera_id": camera_id,
                "timestamp": datetime.utcnow().isoformat(),
            }).encode()

            # Minimal MQTT PUBLISH (QoS 0, no auth)
            def encode_string(s: bytes) -> bytes:
                return len(s).to_bytes(2, "big") + s

            var_header = encode_string(topic)
            packet = bytes([0x30, len(var_header) + len(payload)]) + var_header + payload

            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=5
            )
            # CONNECT packet
            connect = (
                b"\x10\x1a"                 # CONNECT, length 26
                b"\x00\x04MQTT"             # protocol name
                b"\x04"                     # protocol level 3.1.1
                b"\x02"                     # connect flags: clean session
                b"\x00\x3c"                 # keep-alive 60s
                b"\x00\x08oeil"        # client id
            )
            writer.write(connect)
            await writer.drain()
            await asyncio.wait_for(reader.read(4), timeout=3)  # CONNACK
            writer.write(packet)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            logger.info(f"MQTT published to {topic.decode()}")
        except Exception as e:
            logger.debug(f"MQTT publish failed: {e}")
