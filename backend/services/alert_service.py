# Copyright (c) 2026 Mathieu Cadi — Openema SARL
"""
Oeil — Alert Service
Sends email alerts when AI detects unknown persons or vehicles.
Includes camera snapshot as attachment.
Throttling: max 1 email per camera per 10 minutes.
"""
from __future__ import annotations
import asyncio
import logging
import smtplib
import time
from datetime import datetime
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, Optional

import aiohttp

from config import Settings

logger = logging.getLogger("oeil.alert")

# Minimum minutes between emails per camera
EMAIL_THROTTLE_MINUTES = 10


class AlertService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._last_email: Dict[str, float] = {}

    def is_configured(self) -> bool:
        return bool(
            self.settings.OW_SMTP_HOST and
            self.settings.OW_SMTP_USER and
            self.settings.OW_SMTP_PASS and
            self.settings.OW_ALERT_EMAIL
        )

    async def send_alert(self, camera_name: str, camera_id: str,
                          event_type: str, obj_class: str,
                          confidence: float, go2rtc_base: str):
        if not self.is_configured():
            logger.debug("SMTP not configured — skipping email alert")
            return

        # Throttle — max 1 email per camera per 10 minutes
        now = time.time()
        last = self._last_email.get(camera_id, 0)
        if now - last < EMAIL_THROTTLE_MINUTES * 60:
            remaining = int((EMAIL_THROTTLE_MINUTES * 60 - (now - last)) / 60)
            logger.debug(f"Email throttled for {camera_name} — {remaining}min remaining")
            return

        self._last_email[camera_id] = now

        # Fetch snapshot
        snapshot = await self._fetch_snapshot(camera_id, go2rtc_base)

        # Build email in executor (blocking SMTP)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._send_email, camera_name, event_type,
            obj_class, confidence, snapshot
        )

    def _send_email(self, camera_name: str, event_type: str,
                    obj_class: str, confidence: float,
                    snapshot: Optional[bytes]):
        now_str = datetime.now().strftime('%d/%m/%Y à %H:%M:%S')

        # Email icons per event type
        icon = {
            'person':  '🚨',
            'vehicle': '🚗',
            'intrusion': '⚠️',
        }.get(event_type, '📷')

        subject = (
            f"{icon} [Oeil VMS] ALERTE {event_type.upper()} — "
            f"{camera_name} — {datetime.now().strftime('%H:%M')}"
        )

        body = f"""
ALERTE DE SÉCURITÉ — OEIL VMS
{'='*50}

Caméra      : {camera_name}
Type        : {event_type.upper()}
Objet       : {obj_class}
Confiance   : {confidence:.0%}
Date/Heure  : {now_str}
Serveur     : 192.9.251.234

{'='*50}
Un {obj_class} inconnu a été détecté dans la zone surveillée.
Tous les enregistrements ont été déclenchés automatiquement.

Connectez-vous à Oeil VMS pour consulter les enregistrements :
http://192.9.251.234

{'='*50}
Oeil VMS — Openema SARL
Système de surveillance IA
        """.strip()

        msg = MIMEMultipart()
        msg['Subject'] = subject
        msg['From']    = self.settings.OW_SMTP_USER
        msg['To']      = self.settings.OW_ALERT_EMAIL
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        # Attach snapshot if available
        if snapshot:
            img = MIMEImage(snapshot, name=f"alerte_{camera_name}.jpg")
            img.add_header('Content-Disposition', 'attachment',
                          filename=f"alerte_{camera_name}.jpg")
            msg.attach(img)

        try:
            with smtplib.SMTP(
                self.settings.OW_SMTP_HOST,
                self.settings.OW_SMTP_PORT,
                timeout=10
            ) as s:
                s.ehlo()
                s.starttls()
                s.login(self.settings.OW_SMTP_USER, self.settings.OW_SMTP_PASS)
                s.send_message(msg)
            logger.info(
                f"Alert email sent: {event_type} on {camera_name} "
                f"to {self.settings.OW_ALERT_EMAIL}"
            )
        except Exception as e:
            logger.error(f"Failed to send alert email: {e}")

    async def _fetch_snapshot(self, camera_id: str,
                               go2rtc_base: str) -> Optional[bytes]:
        url = f"{go2rtc_base}/api/frame.jpeg?src={camera_id}"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    url, timeout=aiohttp.ClientTimeout(total=4)
                ) as r:
                    if r.status == 200:
                        return await r.read()
        except Exception as e:
            logger.debug(f"Snapshot fetch error: {e}")
        return None
