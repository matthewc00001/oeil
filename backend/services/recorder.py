# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""
Oeil — Recorder Service
FFmpeg-based recording engine. Triggered by motion/AI events from cameras.
Supports pre-roll buffer, post-event hold, and continuous recording.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from database import Recording, RecordingTrigger, Camera, AsyncSessionLocal
from services.event_bus import EventBus
from config import Settings

logger = logging.getLogger("oeil.recorder")


class ActiveRecording:
    def __init__(self, camera_id: str, filepath: Path, process: asyncio.subprocess.Process):
        self.camera_id = camera_id
        self.filepath = filepath
        self.process = process
        self.started_at = datetime.utcnow()
        self.stop_timer: Optional[asyncio.TimerHandle] = None
        self.recording_id: str = str(uuid.uuid4())
        self.has_person = False
        self.has_vehicle = False
        self.has_intrusion = False


class RecorderService:
    def __init__(self, settings: Settings, event_bus: EventBus):
        self.settings = settings
        self.event_bus = event_bus
        self._active: Dict[str, ActiveRecording] = {}  # camera_id -> ActiveRecording
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self):
        self._running = True
        # Listen to event bus for camera events
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.event_bus.subscribe(self._queue)
        self._tasks.append(asyncio.create_task(self._event_consumer()))
        self._tasks.append(asyncio.create_task(self._storage_watchdog()))
        logger.info("Recorder service started")

    async def stop(self):
        self._running = False
        self.event_bus.unsubscribe(self._queue)
        for t in self._tasks:
            t.cancel()
        # Stop all active recordings
        for cam_id in list(self._active.keys()):
            await self._stop_recording(cam_id)

    # ── Event consumer ─────────────────────────────────────────────────────────

    async def _event_consumer(self):
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return

            event_type = event.get("event_type", "")
            camera_id = event.get("camera_id", "")

            if not camera_id or event.get("type") != "camera_event":
                continue

            if event_type in ("motion", "intrusion", "person", "vehicle", "line_crossing", "anpr"):
                await self.trigger_event_recording(camera_id, event_type)

                # Update metadata on active recording
                if camera_id in self._active:
                    rec = self._active[camera_id]
                    if event_type == "person":
                        rec.has_person = True
                    elif event_type == "vehicle":
                        rec.has_vehicle = True
                    elif event_type == "intrusion":
                        rec.has_intrusion = True

    # ── Recording control ──────────────────────────────────────────────────────

    async def trigger_event_recording(self, camera_id: str, trigger: str):
        """Start or extend a recording for this camera."""
        if not self.settings.OW_RECORD_ON_MOTION:
            return

        if camera_id in self._active:
            # Already recording — extend the stop timer
            rec = self._active[camera_id]
            if rec.stop_timer:
                rec.stop_timer.cancel()
            loop = asyncio.get_event_loop()
            rec.stop_timer = loop.call_later(
                self.settings.OW_POST_MOTION_SECONDS,
                lambda: asyncio.create_task(self._stop_recording(camera_id))
            )
            logger.debug(f"Extended recording for {camera_id}")
            return

        # Start new recording
        await self._start_recording(camera_id, RecordingTrigger.event)

    async def _start_recording(self, camera_id: str, trigger: RecordingTrigger):
        """Launch FFmpeg to record from go2rtc HLS/RTSP stream."""
        camera = await self._get_camera(camera_id)
        if not camera:
            logger.warning(f"Cannot record: camera {camera_id} not found in DB")
            return

        # Directory per camera
        rec_dir = self.settings.OW_RECORDINGS_DIR / camera_id
        rec_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}_{trigger.value}.mp4"
        filepath = rec_dir / filename

        # Pull from go2rtc's RTSP re-stream (always available, auth-stripped)
        go2rtc_rtsp = f"rtsp://127.0.0.1:8554/{camera_id}"

        # FFmpeg command: copy video stream (no re-encode), encode audio as AAC
        cmd = [
            "ffmpeg",
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", go2rtc_rtsp,
            "-c:v", "copy",           # no re-encode — camera does H.264/H.265
            "-c:a", "aac",
            "-movflags", "+faststart",
            "-t", str(self.settings.OW_SEGMENT_DURATION),
            "-y",
            str(filepath),
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )

            rec = ActiveRecording(camera_id, filepath, proc)
            self._active[camera_id] = rec

            # Persist recording record to DB immediately
            await self._persist_recording_start(rec, camera, filename, str(filepath), trigger)

            logger.info(f"Recording started: {camera.name} → {filename}")

            # Set auto-stop timer
            loop = asyncio.get_event_loop()
            rec.stop_timer = loop.call_later(
                self.settings.OW_POST_MOTION_SECONDS,
                lambda: asyncio.create_task(self._stop_recording(camera_id))
            )

            # Monitor process in background
            asyncio.create_task(self._monitor_process(camera_id))

        except Exception as e:
            logger.error(f"Failed to start FFmpeg for {camera_id}: {e}")

    async def _stop_recording(self, camera_id: str):
        rec = self._active.pop(camera_id, None)
        if not rec:
            return

        if rec.stop_timer:
            rec.stop_timer.cancel()

        try:
            rec.process.terminate()
            await asyncio.wait_for(rec.process.wait(), timeout=10)
        except Exception:
            try:
                rec.process.kill()
            except Exception:
                pass

        ended_at = datetime.utcnow()
        duration = (ended_at - rec.started_at).total_seconds()
        size_bytes = rec.filepath.stat().st_size if rec.filepath.exists() else 0

        await self._persist_recording_end(rec, ended_at, duration, size_bytes)

        # Generate thumbnail
        asyncio.create_task(self._generate_thumbnail(rec))

        # Publish event
        await self.event_bus.publish({
            "type": "recording_saved",
            "camera_id": camera_id,
            "recording_id": rec.recording_id,
            "filename": rec.filepath.name,
            "duration": duration,
            "timestamp": ended_at.isoformat(),
        })

        logger.info(f"Recording saved: {rec.filepath.name} ({duration:.0f}s, {size_bytes/1024/1024:.1f} MB)")

    async def _monitor_process(self, camera_id: str):
        rec = self._active.get(camera_id)
        if not rec:
            return
        await rec.process.wait()
        # If FFmpeg exited on its own (e.g. stream dropped)
        if camera_id in self._active:
            await self._stop_recording(camera_id)

    # ── Manual / scheduled recording ──────────────────────────────────────────

    async def start_manual(self, camera_id: str) -> bool:
        if camera_id in self._active:
            return False
        await self._start_recording(camera_id, RecordingTrigger.manual)
        return True

    async def stop_manual(self, camera_id: str) -> bool:
        if camera_id not in self._active:
            return False
        await self._stop_recording(camera_id)
        return True

    def is_recording(self, camera_id: str) -> bool:
        return camera_id in self._active

    # ── Thumbnail generation ───────────────────────────────────────────────────

    async def _generate_thumbnail(self, rec: ActiveRecording):
        if not rec.filepath.exists():
            return
        thumb_path = self.settings.OW_SNAPSHOTS_DIR / f"{rec.recording_id}.jpg"
        self.settings.OW_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-loglevel", "error",
            "-i", str(rec.filepath),
            "-ss", "00:00:02",
            "-vframes", "1",
            "-vf", "scale=320:-1",
            "-y", str(thumb_path),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.wait_for(proc.wait(), timeout=15)
            # Update DB
            async with AsyncSessionLocal() as session:
                from sqlmodel import select
                result = await session.exec(
                    select(Recording).where(Recording.id == rec.recording_id)
                )
                db_rec = result.first()
                if db_rec:
                    db_rec.thumbnail_path = str(thumb_path)
                    await session.commit()
        except Exception as e:
            logger.debug(f"Thumbnail generation failed: {e}")

    # ── Storage watchdog ───────────────────────────────────────────────────────

    async def _storage_watchdog(self):
        """Delete oldest recordings when storage limit is reached."""
        while self._running:
            await asyncio.sleep(3600)  # check hourly
            try:
                rec_dir = self.settings.OW_RECORDINGS_DIR
                if not rec_dir.exists():
                    continue
                total_bytes = sum(
                    f.stat().st_size for f in rec_dir.rglob("*.mp4") if f.is_file()
                )
                max_bytes = self.settings.OW_MAX_STORAGE_GB * 1024 ** 3
                if total_bytes > max_bytes:
                    files = sorted(rec_dir.rglob("*.mp4"), key=lambda f: f.stat().st_mtime)
                    for f in files:
                        if total_bytes <= max_bytes * 0.9:
                            break
                        total_bytes -= f.stat().st_size
                        f.unlink()
                        logger.info(f"Storage cleanup: deleted {f.name}")
            except Exception as e:
                logger.error(f"Storage watchdog error: {e}")

    # ── DB helpers ─────────────────────────────────────────────────────────────

    async def _get_camera(self, camera_id: str) -> Optional[Camera]:
        async with AsyncSessionLocal() as session:
            from sqlmodel import select
            result = await session.exec(select(Camera).where(Camera.id == camera_id))
            return result.first()

    async def _persist_recording_start(
        self, rec: ActiveRecording, camera: Camera,
        filename: str, filepath: str, trigger: RecordingTrigger
    ):
        async with AsyncSessionLocal() as session:
            db_rec = Recording(
                id=rec.recording_id,
                camera_id=camera.id,
                filename=filename,
                filepath=filepath,
                trigger=trigger,
                started_at=rec.started_at,
            )
            session.add(db_rec)
            await session.commit()

    async def _persist_recording_end(
        self, rec: ActiveRecording, ended_at: datetime, duration: float, size_bytes: int
    ):
        async with AsyncSessionLocal() as session:
            from sqlmodel import select
            result = await session.exec(select(Recording).where(Recording.id == rec.recording_id))
            db_rec = result.first()
            if db_rec:
                db_rec.ended_at = ended_at
                db_rec.duration_seconds = duration
                db_rec.size_bytes = size_bytes
                db_rec.has_person = rec.has_person
                db_rec.has_vehicle = rec.has_vehicle
                db_rec.has_intrusion = rec.has_intrusion
                await session.commit()
