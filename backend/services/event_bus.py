# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""
Oeil — Supporting Services
  EventBus      : in-process pub/sub for real-time events → WebSocket
  Go2RTCClient  : manage go2rtc streams via its REST API
  NotificationService : email + webhook + MQTT alerts
"""
from __future__ import annotations

import asyncio
import json
import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from typing import Set
from datetime import datetime

import aiohttp

logger = logging.getLogger("oeil.services")


# ── EventBus ──────────────────────────────────────────────────────────────────

class EventBus:
    """Simple async pub/sub: services publish dicts, WS handler subscribes."""

    def __init__(self):
        self._subscribers: Set[asyncio.Queue] = set()

    def subscribe(self, queue: asyncio.Queue):
        self._subscribers.add(queue)

    def unsubscribe(self, queue: asyncio.Queue):
        self._subscribers.discard(queue)

    async def publish(self, event: dict):
        dead = set()
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # slow consumer — drop
            except Exception:
                dead.add(q)
        self._subscribers -= dead


# ── Go2RTCClient ──────────────────────────────────────────────────────────────

class Go2RTCClient:
    """
    Thin async wrapper around go2rtc's REST API.
    go2rtc handles: RTSP ingestion, HLS/WebRTC output, stream re-encoding.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def add_stream(self, name: str, source: str) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    f"{self.base_url}/api/streams",
                    json={name: source},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    return resp.status < 400
        except Exception as e:
            logger.debug(f"go2rtc add_stream error: {e}")
            return False

    async def remove_stream(self, name: str) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    f"{self.base_url}/api/streams",
                    params={"src": name},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    return resp.status < 400
        except Exception as e:
            logger.debug(f"go2rtc remove_stream error: {e}")
            return False

    async def get_streams(self) -> dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/api/streams",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception:
            pass
        return {}

    async def get_stream_info(self, name: str) -> dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/api/streams",
                    params={"src": name},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception:
            pass
        return {}

    def hls_url(self, name: str) -> str:
        return f"{self.base_url}/api/stream.m3u8?src={name}"

    def webrtc_url(self, name: str) -> str:
        return f"{self.base_url}/api/webrtc?src={name}"

    def rtsp_url(self, name: str) -> str:
        # go2rtc re-streams on port 8554
        host = self.base_url.split("://")[-1].split(":")[0]
        return f"rtsp://{host}:8554/{name}"

    def snapshot_url(self, name: str) -> str:
        return f"{self.base_url}/api/frame.jpeg?src={name}"

    async def is_available(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/api/streams",
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False


# ── NotificationService ───────────────────────────────────────────────────────

class NotificationService:
    def __init__(self, settings):
        self.settings = settings

    async def send_alert(self, title: str, body: str, camera_id: str = ""):
        """Fire-and-forget: email + webhook + MQTT in parallel."""
        tasks = []

        if self.settings.OW_SMTP_HOST and self.settings.OW_ALERT_EMAIL:
            tasks.append(asyncio.create_task(
                self._send_email(title, body)
            ))

        if self.settings.OW_WEBHOOK_URL:
            tasks.append(asyncio.create_task(
                self._send_webhook(title, body, camera_id)
            ))

        if self.settings.OW_MQTT_URL:
            tasks.append(asyncio.create_task(
                self._send_mqtt(title, body, camera_id)
            ))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_email(self, subject: str, body: str):
        try:
            msg = MIMEText(body)
            msg["Subject"] = f"[Oeil] {subject}"
            msg["From"] = self.settings.OW_SMTP_USER
            msg["To"] = self.settings.OW_ALERT_EMAIL

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._smtp_send, msg)
            logger.info(f"Email alert sent: {subject}")
        except Exception as e:
            logger.error(f"Email send failed: {e}")

    def _smtp_send(self, msg: MIMEText):
        context = ssl.create_default_context()
        with smtplib.SMTP(self.settings.OW_SMTP_HOST, self.settings.OW_SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(self.settings.OW_SMTP_USER, self.settings.OW_SMTP_PASS)
            server.send_message(msg)

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
                    logger.info(f"Webhook sent: {resp.status}")
        except Exception as e:
            logger.error(f"Webhook failed: {e}")

    async def _send_mqtt(self, title: str, body: str, camera_id: str):
        """Basic MQTT publish via TCP (avoids paho dependency)."""
        try:
            # Simple MQTT PUBLISH without a library
            # For production, add paho-mqtt to requirements
            logger.info(f"MQTT alert (not yet implemented): {title}")
        except Exception as e:
            logger.debug(f"MQTT failed: {e}")
