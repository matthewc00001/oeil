# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""
Oeil — ANPR Service
Stores, indexes and queries license plate detections from Ability VS12100/VS12112 cameras.
Ability ANPR cameras push plate data via HTTP webhook payload:
  {
    "event_type": "anpr",
    "plate_number": "ABC-1234",
    "confidence": 0.97,
    "direction": "entering",
    "lane": 1,
    "image_b64": "...",   # optional JPEG snapshot from camera
    "camera_id": "...",
    "timestamp": "2025-01-01T12:00:00Z"
  }
"""
from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from sqlmodel import Field, SQLModel, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal
from config import settings

logger = logging.getLogger("oeil.anpr")


# ── ANPR Database Model ────────────────────────────────────────────────────────

class PlateDetection(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    camera_id: str
    camera_name: str = ""
    plate_number: str = Field(index=True)
    plate_normalized: str = Field(index=True)  # uppercased, stripped
    confidence: float = 1.0
    direction: str = ""        # "entering" | "exiting" | ""
    lane: int = 0
    snapshot_path: str = ""    # local JPEG from camera
    recording_id: str = ""
    event_id: str = ""
    # Watchlist match
    watchlist_match: bool = False
    watchlist_tag: str = ""    # e.g. "VIP", "Blocked", "Staff"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PlateWatchlist(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    plate_number: str = Field(index=True)
    plate_normalized: str = Field(index=True)
    tag: str = "flagged"       # "VIP" | "Staff" | "Blocked" | custom
    notes: str = ""
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── ANPR Service ──────────────────────────────────────────────────────────────

class ANPRService:
    def __init__(self, event_bus, notification_service, snapshot_dir: Path):
        self.event_bus = event_bus
        self.notifications = notification_service
        self.snapshot_dir = snapshot_dir / "anpr"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._watchlist_cache: dict[str, PlateWatchlist] = {}

    async def start(self):
        await self._load_watchlist()
        logger.info("ANPR service started")

    async def _load_watchlist(self):
        async with AsyncSessionLocal() as session:
            result = await session.exec(
                select(PlateWatchlist).where(PlateWatchlist.active == True)
            )
            for entry in result.all():
                self._watchlist_cache[entry.plate_normalized] = entry
        logger.info(f"Watchlist loaded: {len(self._watchlist_cache)} entries")

    async def process_detection(self, payload: dict, camera_id: str, camera_name: str = "") -> PlateDetection:
        """Process an ANPR event from a camera webhook."""
        plate_raw = str(payload.get("plate_number", "")).strip()
        plate_norm = self._normalize(plate_raw)
        confidence = float(payload.get("confidence", 1.0))
        direction = payload.get("direction", "")
        lane = int(payload.get("lane", 0))
        image_b64 = payload.get("image_b64", "")

        # Save snapshot if camera sent one
        snapshot_path = ""
        if image_b64:
            snapshot_path = await self._save_snapshot(plate_norm, image_b64)

        # Watchlist check
        watchlist_entry = self._watchlist_cache.get(plate_norm)
        watchlist_match = watchlist_entry is not None
        watchlist_tag = watchlist_entry.tag if watchlist_entry else ""

        detection = PlateDetection(
            camera_id=camera_id,
            camera_name=camera_name,
            plate_number=plate_raw,
            plate_normalized=plate_norm,
            confidence=confidence,
            direction=direction,
            lane=lane,
            snapshot_path=snapshot_path,
            watchlist_match=watchlist_match,
            watchlist_tag=watchlist_tag,
        )

        async with AsyncSessionLocal() as session:
            session.add(detection)
            await session.commit()
            await session.refresh(detection)

        # Publish to event bus for WebSocket
        await self.event_bus.publish({
            "type": "anpr",
            "camera_id": camera_id,
            "camera_name": camera_name,
            "plate_number": plate_raw,
            "confidence": confidence,
            "direction": direction,
            "watchlist_match": watchlist_match,
            "watchlist_tag": watchlist_tag,
            "snapshot_path": snapshot_path,
            "timestamp": detection.created_at.isoformat(),
        })

        # Alert on watchlist match
        if watchlist_match:
            tag = watchlist_tag or "flagged"
            await self.notifications.send_alert(
                title=f"⚠ Watchlist plate detected — {plate_raw}",
                body=f"Tag: {tag} | Camera: {camera_name} | Direction: {direction} | Confidence: {confidence:.0%}",
                camera_id=camera_id,
            )
            logger.warning(f"ANPR watchlist match: {plate_raw} [{tag}] on {camera_name}")
        else:
            logger.info(f"ANPR: {plate_raw} ({confidence:.0%}) on {camera_name}")

        return detection

    async def _save_snapshot(self, plate_norm: str, image_b64: str) -> str:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}_{plate_norm}.jpg"
        path = self.snapshot_dir / filename
        try:
            data = base64.b64decode(image_b64)
            path.write_bytes(data)
            return str(path)
        except Exception as e:
            logger.debug(f"Failed to save ANPR snapshot: {e}")
            return ""

    @staticmethod
    def _normalize(plate: str) -> str:
        """Uppercase, remove spaces/dashes/dots for consistent matching."""
        return "".join(c for c in plate.upper() if c.isalnum())

    # ── Watchlist management ───────────────────────────────────────────────────

    async def add_to_watchlist(self, plate: str, tag: str = "flagged", notes: str = "") -> PlateWatchlist:
        norm = self._normalize(plate)
        entry = PlateWatchlist(
            plate_number=plate.strip(),
            plate_normalized=norm,
            tag=tag,
            notes=notes,
        )
        async with AsyncSessionLocal() as session:
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
        self._watchlist_cache[norm] = entry
        logger.info(f"Added to watchlist: {plate} [{tag}]")
        return entry

    async def remove_from_watchlist(self, plate_id: str) -> bool:
        async with AsyncSessionLocal() as session:
            result = await session.exec(
                select(PlateWatchlist).where(PlateWatchlist.id == plate_id)
            )
            entry = result.first()
            if not entry:
                return False
            entry.active = False
            await session.commit()
        # Remove from cache
        self._watchlist_cache.pop(entry.plate_normalized, None)
        return True

    async def get_watchlist(self) -> List[PlateWatchlist]:
        async with AsyncSessionLocal() as session:
            result = await session.exec(
                select(PlateWatchlist).where(PlateWatchlist.active == True)
                .order_by(PlateWatchlist.created_at.desc())
            )
            return result.all()

    # ── Queries ────────────────────────────────────────────────────────────────

    async def search_plates(
        self,
        plate: Optional[str] = None,
        camera_id: Optional[str] = None,
        watchlist_only: bool = False,
        since: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[PlateDetection]:
        async with AsyncSessionLocal() as session:
            q = select(PlateDetection).order_by(PlateDetection.created_at.desc())
            if plate:
                norm = self._normalize(plate)
                q = q.where(PlateDetection.plate_normalized.contains(norm))
            if camera_id:
                q = q.where(PlateDetection.camera_id == camera_id)
            if watchlist_only:
                q = q.where(PlateDetection.watchlist_match == True)
            if since:
                q = q.where(PlateDetection.created_at >= since)
            q = q.offset(offset).limit(limit)
            result = await session.exec(q)
            return result.all()

    async def get_plate_history(self, plate: str, days: int = 30) -> List[PlateDetection]:
        norm = self._normalize(plate)
        since = datetime.utcnow() - timedelta(days=days)
        async with AsyncSessionLocal() as session:
            result = await session.exec(
                select(PlateDetection)
                .where(PlateDetection.plate_normalized == norm)
                .where(PlateDetection.created_at >= since)
                .order_by(PlateDetection.created_at.desc())
            )
            return result.all()

    async def get_stats(self) -> dict:
        async with AsyncSessionLocal() as session:
            all_detections = await session.exec(select(PlateDetection))
            detections = all_detections.all()
            watchlist_hits = [d for d in detections if d.watchlist_match]
            today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            today_detections = [d for d in detections if d.created_at >= today]
        return {
            "total_detections": len(detections),
            "today_detections": len(today_detections),
            "watchlist_hits": len(watchlist_hits),
            "unique_plates": len({d.plate_normalized for d in detections}),
        }
