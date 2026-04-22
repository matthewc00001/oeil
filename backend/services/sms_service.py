# Copyright (c) 2026 Mathieu Cadi — Openema SARL
"""
Oeil — SMS Alert Service (Twilio)
Sends SMS alerts when AI detects unknown persons or vehicles.
All credentials are AES-256 encrypted — never in plaintext in code or GitHub.
Throttling: max 1 SMS per camera per 15 minutes.
"""
from __future__ import annotations
import asyncio
import logging
import time
from datetime import datetime
from typing import Dict

logger = logging.getLogger("oeil.sms")

SMS_THROTTLE_MINUTES = 15


class SMSService:
    def __init__(self):
        self._last_sms: Dict[str, float] = {}
        self._client = None
        self._from_number = None
        self._to_number = None
        self._ready = False
        self._init()

    def _init(self):
        try:
            import sys
            sys.path.insert(0, '/opt/oeil')
            from services.crypto_service import get_decrypted_env
            from twilio.rest import Client

            sid   = get_decrypted_env('TWILIO_ACCOUNT_SID')
            token = get_decrypted_env('TWILIO_AUTH_TOKEN')
            self._from_number = get_decrypted_env('TWILIO_FROM_NUMBER')
            self._to_number   = get_decrypted_env('TWILIO_TO_NUMBER')

            if sid and token and self._from_number and self._to_number:
                self._client = Client(sid, token)
                self._ready = True
                logger.info("SMS service ready (Twilio)")
            else:
                logger.warning("SMS service: missing Twilio credentials")
        except Exception as e:
            logger.error(f"SMS service init failed: {e}")

    async def send_alert(self, camera_name: str, camera_id: str,
                          event_type: str, obj_class: str,
                          all_camera_ids: list = None):
        if not self._ready:
            return

        now = time.time()
        last = self._last_sms.get(camera_id, 0)
        if now - last < SMS_THROTTLE_MINUTES * 60:
            remaining = int((SMS_THROTTLE_MINUTES * 60 - (now - last)) / 60)
            logger.debug(f"SMS throttled for {camera_name} — {remaining}min remaining")
            return

        self._last_sms[camera_id] = now

        now_str = datetime.now().strftime('%d/%m/%Y %H:%M')
        icon = '🚨' if event_type == 'person' else '🚗'
        from services.crypto_service import get_decrypted_env
        tunnel = get_decrypted_env('TUNNEL_URL', 'http://192.9.251.234')
        cams   = ','.join(all_camera_ids) if all_camera_ids else camera_id
        url    = f"{tunnel}/alert?cam={camera_id}&cams={cams}&type={event_type}"
        body   = (
            f"{icon} OEIL VMS ALERTE\n"
            f"Camera: {camera_name}\n"
            f"Type: {event_type.upper()}\n"
            f"Heure: {now_str}\n"
            f"Voir live: {url}"
        )

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._send, body)

    def _send(self, body: str):
        try:
            message = self._client.messages.create(
                body=body,
                from_=self._from_number,
                to=self._to_number
            )
            logger.info(f"SMS sent: {message.sid}")
        except Exception as e:
            logger.error(f"SMS send failed: {e}")
