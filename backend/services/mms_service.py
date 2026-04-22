# Copyright (c) 2026 Mathieu Cadi — Openema SARL
"""
Oeil — MMS Alert Service (Twilio)
Sends compressed video clip via MMS when AI detects an event.
One MMS per triggered camera, 30 seconds max, 640x360 resolution.
All credentials AES-256 encrypted.
"""
from __future__ import annotations
import asyncio
import logging
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import List

logger = logging.getLogger("oeil.mms")

CLIP_DURATION  = 30   # seconds
CLIP_WIDTH     = 640
CLIP_HEIGHT    = 360
CLIP_CRF       = 28   # compression quality
MMS_THROTTLE   = 900  # 15 minutes between MMS per camera
CLIPS_DIR      = Path("/var/lib/oeil/clips")  # compressed clips for MMS

class MMSService:
    def __init__(self):
        self._last_mms: dict = {}
        self._client       = None
        self._from_number  = None
        self._to_number    = None
        self._tunnel_url   = None
        self._ready        = False
        CLIPS_DIR.mkdir(parents=True, exist_ok=True)
        self._init()

    def _init(self):
        try:
            import sys
            sys.path.insert(0, '/opt/oeil')
            from services.crypto_service import get_decrypted_env
            from twilio.rest import Client

            sid              = get_decrypted_env('TWILIO_ACCOUNT_SID')
            token            = get_decrypted_env('TWILIO_AUTH_TOKEN')
            self._from_number = get_decrypted_env('TWILIO_FROM_NUMBER')
            self._to_number   = get_decrypted_env('TWILIO_TO_NUMBER')
            self._tunnel_url  = get_decrypted_env('TUNNEL_URL', 'http://192.9.251.234')

            if sid and token and self._from_number and self._to_number:
                self._client = Client(sid, token)
                self._ready  = True
                logger.info("MMS service ready (Twilio)")
            else:
                logger.warning("MMS service: missing Twilio credentials")
        except Exception as e:
            logger.error(f"MMS service init failed: {e}")

    async def send_alert(self, camera_name: str, camera_id: str,
                         event_type: str, recording_path: str = None):
        """Send MMS with compressed video clip for this camera."""
        if not self._ready:
            return

        now = time.time()
        if now - self._last_mms.get(camera_id, 0) < MMS_THROTTLE:
            logger.debug(f"MMS throttled for {camera_name}")
            return

        self._last_mms[camera_id] = now

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._process_and_send, camera_name, camera_id, event_type, recording_path
        )

    def _process_and_send(self, camera_name: str, camera_id: str,
                           event_type: str, recording_path: str = None):
        try:
            now_str  = datetime.now().strftime('%d/%m/%Y %H:%M')
            icon     = '🚨' if event_type == 'person' else '🚗'

            # Find the most recent recording for this camera if not provided
            if not recording_path:
                rec_dir = Path(f"/var/lib/oeil/recordings/{camera_id}")
                if rec_dir.exists():
                    clips = sorted(rec_dir.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
                    if clips:
                        recording_path = str(clips[0])

            media_url = None

            if recording_path and Path(recording_path).exists():
                # Compress clip for MMS
                clip_name = f"alert_{camera_id[:8]}_{int(time.time())}.mp4"
                clip_path = CLIPS_DIR / clip_name

                cmd = [
                    'ffmpeg', '-i', recording_path,
                    '-vf', f'scale={CLIP_WIDTH}:{CLIP_HEIGHT}',
                    '-c:v', 'libx264', '-crf', str(CLIP_CRF), '-preset', 'fast',
                    '-an', '-t', str(CLIP_DURATION),
                    str(clip_path), '-y'
                ]
                result = subprocess.run(cmd, capture_output=True, timeout=30)

                if result.returncode == 0 and clip_path.exists():
                    size_kb = clip_path.stat().st_size / 1024
                    logger.info(f"Clip compressed: {size_kb:.0f} KB")
                    media_url = f"{self._tunnel_url}/clips/{clip_name}"

            # Send MMS
            body = (
                f"{icon} OEIL VMS ALERTE\n"
                f"Camera: {camera_name}\n"
                f"Type: {event_type.upper()}\n"
                f"Heure: {now_str}"
            )

            kwargs = dict(
                body=body,
                from_=self._from_number,
                to=self._to_number,
            )
            if media_url:
                kwargs['media_url'] = [media_url]

            message = self._client.messages.create(**kwargs)
            logger.info(f"MMS sent: {message.sid} — {camera_name}")

            # Cleanup old clips (keep last 20)
            clips = sorted(CLIPS_DIR.glob("*.mp4"), key=lambda f: f.stat().st_mtime)
            for old_clip in clips[:-20]:
                old_clip.unlink()

        except Exception as e:
            logger.error(f"MMS send failed: {e}")
