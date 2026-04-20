# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
"""
Oeil — Motion Detection Service
Grabs JPEG frames from go2rtc, compares with OpenCV,
checks motion against per-camera polygon zones,
publishes events to EventBus → RecorderService picks them up.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Dict, Optional

import aiohttp
import cv2
import numpy as np

from config import Settings
from database import Camera, AsyncSessionLocal
from services.event_bus import EventBus

logger = logging.getLogger("oeil.motion")

# How often to grab a frame per camera (seconds)
FRAME_INTERVAL = 0.5   # 2 fps — light on CPU
# Minimum seconds between motion events per camera (cooldown)
MOTION_COOLDOWN = 15.0


class CameraMotionState:
    def __init__(self):
        self.prev_gray: Optional[np.ndarray] = None
        self.last_motion_at: float = 0.0
        self.motion_active: bool = False


class MotionDetectorService:
    def __init__(self, settings: Settings, event_bus: EventBus):
        self.settings = settings
        self.event_bus = event_bus
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._states: Dict[str, CameraMotionState] = {}
        self._go2rtc_base = settings.OW_GO2RTC_API.rstrip("/")

    async def start(self):
        self._running = True
        self._tasks.append(asyncio.create_task(self._main_loop()))
        logger.info("Motion detector started")

    async def stop(self):
        self._running = False
        for t in self._tasks:
            t.cancel()
        logger.info("Motion detector stopped")

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _main_loop(self):
        """Every FRAME_INTERVAL seconds, fetch a frame for each armed camera."""
        while self._running:
            try:
                cameras = await self._get_armed_cameras()
                tasks = [self._process_camera(cam) for cam in cameras]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Motion loop error: {e}")
            await asyncio.sleep(FRAME_INTERVAL)

    # ── Per-camera processing ─────────────────────────────────────────────────

    async def _process_camera(self, cam: Camera):
        cam_id = cam.id
        if not cam.motion_enabled or not cam.armed:
            return

        # Fetch JPEG frame from go2rtc
        frame = await self._fetch_frame(cam_id)
        if frame is None:
            return

        # Convert to grayscale
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        state = self._states.setdefault(cam_id, CameraMotionState())

        if state.prev_gray is None:
            state.prev_gray = gray
            return

        # Frame difference
        delta = cv2.absdiff(state.prev_gray, gray)
        state.prev_gray = gray

        # Threshold
        _, thresh = cv2.threshold(delta, 25, 255, cv2.THRESH_BINARY)
        thresh = cv2.dilate(thresh, None, iterations=2)

        # Load zones for this camera
        zones = await self._get_zones(cam)

        if zones:
            motion_detected = self._check_zones(thresh, zones)
        else:
            # No zones defined — check whole frame
            motion_pixels = cv2.countNonZero(thresh)
            total_pixels = thresh.shape[0] * thresh.shape[1]
            motion_pct = motion_pixels / total_pixels
            motion_detected = motion_pct > 0.02   # 2% of frame

        now = time.time()
        if motion_detected:
            if not state.motion_active or (now - state.last_motion_at) > MOTION_COOLDOWN:
                state.motion_active = True
                state.last_motion_at = now
                # Only record during active periods
                from datetime import datetime as _dt
                _now = _dt.now()
                _t   = _now.hour * 60 + _now.minute
                _wd  = _now.weekday()
                _is_weekend    = _wd in (5, 6)
                _is_morning    = 7*60+45 <= _t <= 9*60+30 and _wd < 5
                _is_end_of_day = 17*60 <= _t <= 18*60+30 and _wd < 5
                _is_night      = (_t >= 18*60+30 or _t < 8*60) and _wd < 5
                if _is_weekend or _is_morning or _is_end_of_day or _is_night:
                    await self._publish_motion(cam)
                else:
                    logger.debug(f'Motion on {cam.name} — outside active hours')
            if state.motion_active and (now - state.last_motion_at) > MOTION_COOLDOWN:
                state.motion_active = False

    # ── Zone checking ─────────────────────────────────────────────────────────

    def _check_zones(self, thresh: np.ndarray, zones: list) -> bool:
        """
        For each enabled zone polygon, check if motion pixels fall inside it.
        Returns True if any zone's sensitivity threshold is exceeded.
        """
        h, w = thresh.shape

        for zone in zones:
            if not zone.get("enabled", True):
                continue

            points = zone.get("points", [])
            if len(points) < 3:
                continue

            sensitivity = zone.get("sensitivity", 50) / 100.0

            # Convert normalized points to pixel coords
            poly = np.array(
                [[int(x * w), int(y * h)] for x, y in points],
                dtype=np.int32
            )

            # Create mask for this zone
            mask = np.zeros((h, w), dtype=np.uint8)
            cv2.fillPoly(mask, [poly], 255)

            # Count motion pixels inside the zone
            zone_pixels = cv2.countNonZero(mask)
            if zone_pixels == 0:
                continue

            motion_in_zone = cv2.countNonZero(cv2.bitwise_and(thresh, thresh, mask=mask))
            motion_ratio = motion_in_zone / zone_pixels

            # Sensitivity: lower value = more sensitive (triggers easier)
            # sensitivity=0.1 means 10% of zone must be moving
            threshold = 0.02 + (sensitivity * 0.18)  # range 2%-20%

            if motion_ratio > threshold:
                logger.debug(
                    f"Motion in zone '{zone.get('name')}': "
                    f"{motion_ratio:.1%} > {threshold:.1%}"
                )
                return True

        return False

    # ── Frame fetching ────────────────────────────────────────────────────────

    async def _fetch_frame(self, cam_id: str) -> Optional[np.ndarray]:
        url = f"{self._go2rtc_base}/api/frame.jpeg?src={cam_id}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.read()
                    arr = np.frombuffer(data, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    return frame
        except Exception as e:
            logger.debug(f"Frame fetch error for {cam_id}: {e}")
            return None

    # ── Event publishing ──────────────────────────────────────────────────────

    async def _publish_motion(self, cam: Camera):
        event = {
            "type": "camera_event",
            "camera_id": cam.id,
            "camera_name": cam.name,
            "event_type": "motion",
            "object_class": "motion",
            "confidence": 1.0,
            "timestamp": asyncio.get_event_loop().time(),
        }
        await self.event_bus.publish(event)
        logger.info(f"Motion detected: {cam.name}")

    # ── DB helpers ────────────────────────────────────────────────────────────

    async def _get_armed_cameras(self) -> list[Camera]:
        async with AsyncSessionLocal() as session:
            from sqlmodel import select
            result = await session.execute(
                select(Camera).where(
                    Camera.enabled == True,
                    Camera.armed == True,
                    Camera.motion_enabled == True,
                    Camera.status == "online",
                )
            )
            return list(result.scalars().all())

    async def _get_zones(self, cam: Camera) -> list:
        async with AsyncSessionLocal() as session:
            from sqlmodel import select
            result = await session.execute(
                select(Camera).where(Camera.id == cam.id)
            )
            fresh = result.scalars().first()
            if not fresh:
                return []
            raw = getattr(fresh, "zones_json", None) or "[]"
            try:
                return json.loads(raw)
            except Exception:
                return []
