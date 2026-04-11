# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""
Oeil — Snapshot Service
On-demand JPEG frame capture from go2rtc.
Also handles scheduled snapshots (e.g. one per minute per camera).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiohttp

from config import settings
from database import Camera, AsyncSessionLocal
from sqlmodel import select

logger = logging.getLogger("oeil.snapshot")


class SnapshotService:
    def __init__(self, go2rtc_client, snapshot_dir: Path):
        self.go2rtc = go2rtc_client
        self.snapshot_dir = snapshot_dir
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._scheduled_snapshot_loop())
        logger.info("Snapshot service started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def capture(self, camera_id: str) -> Optional[Path]:
        """
        Pull a JPEG snapshot from go2rtc for a given camera.
        Returns local path on success, None on failure.
        """
        url = self.go2rtc.snapshot_url(camera_id)
        cam_dir = self.snapshot_dir / camera_id
        cam_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        out_path = cam_dir / f"{ts}.jpg"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        out_path.write_bytes(data)
                        return out_path
                    else:
                        logger.debug(f"Snapshot failed for {camera_id}: HTTP {resp.status}")
        except Exception as e:
            logger.debug(f"Snapshot error for {camera_id}: {e}")

        return None

    async def capture_all(self) -> dict:
        """Capture snapshots from all online cameras."""
        async with AsyncSessionLocal() as db:
            result = await db.exec(
                select(Camera).where(Camera.enabled == True)
            )
            cameras = result.all()

        results = {}
        tasks = [(c.id, asyncio.create_task(self.capture(c.id))) for c in cameras]
        for cam_id, task in tasks:
            try:
                path = await asyncio.wait_for(task, timeout=10)
                results[cam_id] = str(path) if path else None
            except Exception:
                results[cam_id] = None
        return results

    def latest_snapshot(self, camera_id: str) -> Optional[Path]:
        """Return the most recent snapshot file for a camera."""
        cam_dir = self.snapshot_dir / camera_id
        if not cam_dir.exists():
            return None
        files = sorted(cam_dir.glob("*.jpg"), key=lambda f: f.stat().st_mtime, reverse=True)
        return files[0] if files else None

    def cleanup_old_snapshots(self, keep_per_camera: int = 1440):
        """Keep only the N most recent snapshots per camera (default = 24h at 1/min)."""
        for cam_dir in self.snapshot_dir.iterdir():
            if not cam_dir.is_dir():
                continue
            files = sorted(cam_dir.glob("*.jpg"), key=lambda f: f.stat().st_mtime, reverse=True)
            for old in files[keep_per_camera:]:
                try:
                    old.unlink()
                except Exception:
                    pass

    async def _scheduled_snapshot_loop(self):
        """Take one snapshot per camera every 60 seconds (for timeline preview)."""
        while self._running:
            await asyncio.sleep(60)
            try:
                await self.capture_all()
                self.cleanup_old_snapshots()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug(f"Scheduled snapshot error: {e}")
