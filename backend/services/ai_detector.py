# Copyright (c) 2026 Mathieu Cadi — Openema SARL
"""
Oeil — AI Detection Service v2
YOLOv8 + Zone filtering + Schedule + Identity (body Re-ID + vehicle color)

Rules:
  LEARNING WINDOW (weekdays 7:45-9:30):
    - Person in zone → learn body vector
    - Vehicle in zone → learn color fingerprint
    - Blue vehicle → always Volvo exemption, skip

  ALERT PERIOD (weekdays 6PM-8AM + full weekend):
    - Blue vehicle → Volvo exemption, skip
    - Known worker vehicle → no alert
    - Unknown vehicle → alert + record ALL cameras
    - Known worker person → no alert
    - Unknown person → alert + record ALL cameras

  DAY (weekdays 9:30AM-6PM outside learning window):
    - Blue vehicle → skip
    - Person in zone → check if known worker
    - Unknown person → alert + record (intruder during work hours)
    - Vehicles ignored during day (normal activity)
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
import warnings
from datetime import datetime
from typing import Dict, Optional
warnings.filterwarnings('ignore')
import aiohttp
import cv2
import numpy as np
from config import Settings
from database import Camera, AsyncSessionLocal
from services.event_bus import EventBus
from services.identity_store import IdentityStore

logger = logging.getLogger("oeil.ai")

# YOLOv8 classes we use
PERSON_CLS  = {0: 'person'}
VEHICLE_CLS = {2: 'car', 3: 'motorcycle', 5: 'bus', 7: 'truck', 1: 'bicycle'}

ANALYSIS_INTERVAL = 3.0
MIN_CONFIDENCE    = 0.45
EVENT_COOLDOWN    = 20.0
MODEL_PATH        = '/opt/oeil/models/yolov8n.pt'

# Cameras that can see vehicles
VEHICLE_CAMERAS = {
    'de864092-48d5-4f6f-b6e4-abd6feb767a2',  # Entree Principale
    'ace2c58be4612a63d383596180afe86f',        # Camera 03
    'd2ca31c62fcaf4f838dfd2577c79d249',        # Camera 04
}


def time_mode() -> str:
    """
    Returns current time mode:
      'learning'  — weekdays 7:45-9:30 (learn workers)
      'alert'     — weekdays 18:00-8:00 + full weekend
      'day'       — weekdays 9:30-18:00 (watch for intruders only)
    """
    now     = datetime.now()
    wd      = now.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
    h, m    = now.hour, now.minute
    t       = h * 60 + m  # minutes since midnight

    # Full weekend
    if wd in (5, 6):
        return 'alert'

    # Friday after 18:00
    if wd == 4 and h >= 18:
        return 'alert'

    # Monday before 8:00
    if wd == 0 and h < 8:
        return 'alert'

    # Weekday night (Tue-Fri before 8AM or Mon-Thu after 18PM)
    if wd in (1, 2, 3) and (h >= 18 or h < 8):
        return 'alert'
    if wd == 0 and h >= 18:
        return 'alert'

    # Learning window: 7:45 - 9:30 weekdays
    if 7 * 60 + 45 <= t <= 9 * 60 + 30:
        return 'learning'

    # Daytime
    return 'day'


class AIDetectorService:
    def __init__(self, settings: Settings, event_bus: EventBus):
        self.settings  = settings
        self.event_bus = event_bus
        self._running  = False
        self._tasks: list[asyncio.Task] = []
        self._last_event: Dict[str, float] = {}
        self._model    = None
        self._store    = IdentityStore()
        self._go2rtc   = settings.OW_GO2RTC_API.rstrip('/')
        self._all_cam_ids: list[str] = []

    async def start(self):
        self._running = True
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_model)
        self._tasks.append(asyncio.create_task(self._main_loop()))
        logger.info("AI detector v2 started (YOLOv8 + ReID + schedule)")

    def _load_model(self):
        from ultralytics import YOLO
        self._model = YOLO(MODEL_PATH)
        logger.info("YOLOv8n model loaded")

    async def stop(self):
        self._running = False
        for t in self._tasks:
            t.cancel()

    async def _main_loop(self):
        while self._running:
            try:
                self._store.check_daily_reset()
                mode    = time_mode()
                cameras = await self._get_armed_cameras()
                self._all_cam_ids = [c.id for c in cameras]

                if mode == 'learning':
                    logger.debug(
                        f"Learning mode — workers: {self._store.worker_count}, "
                        f"vehicles: {self._store.vehicle_count}"
                    )
                elif mode == 'alert':
                    pass  # always active
                # day mode — only watch for unknown persons

                await asyncio.gather(
                    *[self._analyse(c, mode) for c in cameras],
                    return_exceptions=True
                )
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"AI loop error: {e}")
            await asyncio.sleep(ANALYSIS_INTERVAL)

    async def _analyse(self, cam: Camera, mode: str):
        frame = await self._fetch_frame(cam.id)
        if frame is None:
            return

        loop = asyncio.get_event_loop()
        boxes = await loop.run_in_executor(None, self._infer, frame)
        if not boxes:
            return

        zones = await self._get_zones(cam)
        h, w  = frame.shape[:2]

        for det in boxes:
            cx, cy = det['cx'], det['cy']

            # Check zone
            if zones and not self._in_any_zone(cx, cy, zones, w, h):
                continue

            x1, y1, x2, y2 = det['x1'], det['y1'], det['x2'], det['y2']
            crop = frame[max(0,int(y1)):int(y2), max(0,int(x1)):int(x2)]

            if det['type'] == 'vehicle' and cam.id in VEHICLE_CAMERAS:
                await self._handle_vehicle(cam, frame, crop, mode)

            elif det['type'] == 'person':
                await self._handle_person(cam, frame, crop, mode)

    # ── Vehicle handling ──────────────────────────────────────────────────────

    async def _handle_vehicle(self, cam: Camera, frame: np.ndarray,
                               crop: np.ndarray, mode: str):
        loop = asyncio.get_event_loop()

        # Always check for blue Volvo first
        is_volvo = await loop.run_in_executor(
            None, self._store.is_blue_volvo, crop)
        if is_volvo:
            logger.debug(f"Blue Volvo detected on {cam.name} — exempt")
            return

        fingerprint = await loop.run_in_executor(
            None, self._store.compute_vehicle_fingerprint, crop)

        if mode == 'learning':
            # Learn this vehicle as a worker vehicle
            await loop.run_in_executor(
                None, self._store.learn_vehicle, fingerprint, cam.id)
            return

        if mode == 'day':
            return  # ignore vehicles during day

        # Alert mode — check if known
        is_known = await loop.run_in_executor(
            None, self._store.is_known_vehicle, fingerprint)
        if is_known:
            logger.debug(f"Known worker vehicle on {cam.name} — no alert")
            return

        await self._trigger_alert(cam, 'vehicle', 'unknown vehicle', 0.9)

    # ── Person handling ───────────────────────────────────────────────────────

    async def _handle_person(self, cam: Camera, frame: np.ndarray,
                              crop: np.ndarray, mode: str):
        loop = asyncio.get_event_loop()

        vector = await loop.run_in_executor(
            None, self._store.compute_body_vector, crop)

        if mode == 'learning':
            # Learn this person as a worker
            await loop.run_in_executor(
                None, self._store.learn_worker, vector, cam.id)
            return

        # Day or alert — check if known worker
        is_known = await loop.run_in_executor(
            None, self._store.is_known_worker, vector)
        if is_known:
            logger.debug(f"Known worker on {cam.name} — no alert")
            return

        await self._trigger_alert(cam, 'person', 'unknown person', 0.9)

    # ── Alert trigger ─────────────────────────────────────────────────────────

    async def _trigger_alert(self, cam: Camera, event_type: str,
                              obj_class: str, confidence: float):
        now = time.time()
        if now - self._last_event.get(cam.id, 0) < EVENT_COOLDOWN:
            return
        self._last_event[cam.id] = now

        mode_str = datetime.now().strftime('%A %H:%M')
        logger.warning(
            f"ALERT [{event_type}] on {cam.name} "
            f"— {obj_class} [{mode_str}]"
        )

        # Publish event for this camera → triggers recording
        await self.event_bus.publish({
            "type":         "camera_event",
            "camera_id":    cam.id,
            "camera_name":  cam.name,
            "event_type":   event_type,
            "object_class": obj_class,
            "confidence":   confidence,
        })

        # Also trigger recording on ALL other cameras simultaneously
        for other_cam_id in self._all_cam_ids:
            if other_cam_id != cam.id:
                if now - self._last_event.get(other_cam_id, 0) < EVENT_COOLDOWN:
                    continue
                self._last_event[other_cam_id] = now
                await self.event_bus.publish({
                    "type":         "camera_event",
                    "camera_id":    other_cam_id,
                    "camera_name":  f"correlated-{other_cam_id[:8]}",
                    "event_type":   event_type,
                    "object_class": obj_class,
                    "confidence":   confidence,
                })

    # ── Inference ─────────────────────────────────────────────────────────────

    def _infer(self, frame: np.ndarray) -> list:
        if self._model is None:
            return []
        try:
            results = self._model(frame, verbose=False, conf=MIN_CONFIDENCE)
            out = []
            for box in results[0].boxes:
                cls_id = int(box.cls[0])
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                if cls_id in PERSON_CLS:
                    det_type = 'person'
                elif cls_id in VEHICLE_CLS:
                    det_type = 'vehicle'
                else:
                    continue
                out.append({
                    'type':       det_type,
                    'class':      (PERSON_CLS | VEHICLE_CLS)[cls_id],
                    'confidence': float(box.conf[0]),
                    'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
                    'cx': (x1 + x2) / 2,
                    'cy': (y1 + y2) / 2,
                })
            return out
        except Exception as e:
            logger.debug(f"Inference error: {e}")
            return []

    def _in_any_zone(self, cx, cy, zones, w, h) -> bool:
        for zone in zones:
            if not zone.get('enabled', True):
                continue
            pts = zone.get('points', [])
            if len(pts) < 3:
                continue
            poly = np.array(
                [[int(px*w), int(py*h)] for px, py in pts],
                dtype=np.int32)
            if cv2.pointPolygonTest(poly, (cx, cy), False) >= 0:
                return True
        return False

    async def _fetch_frame(self, cam_id: str) -> Optional[np.ndarray]:
        url = f"{self._go2rtc}/api/frame.jpeg?src={cam_id}"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    url, timeout=aiohttp.ClientTimeout(total=4)) as r:
                    if r.status != 200:
                        return None
                    data = await r.read()
                    arr  = np.frombuffer(data, dtype=np.uint8)
                    return cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception as e:
            logger.debug(f"Frame fetch {cam_id}: {e}")
            return None

    async def _get_zones(self, cam: Camera) -> list:
        async with AsyncSessionLocal() as session:
            from sqlmodel import select
            result = await session.execute(
                select(Camera).where(Camera.id == cam.id))
            fresh = result.scalars().first()
            if not fresh:
                return []
            try:
                return json.loads(
                    getattr(fresh, 'zones_json', '[]') or '[]')
            except Exception:
                return []

    async def _get_armed_cameras(self) -> list:
        async with AsyncSessionLocal() as session:
            from sqlmodel import select
            result = await session.execute(
                select(Camera).where(
                    Camera.enabled == True,
                    Camera.armed   == True,
                    Camera.status  == "online",
                ))
            return list(result.scalars().all())
