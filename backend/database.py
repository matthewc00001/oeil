# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""
Oeil — Database Models and Initialization
SQLite via SQLModel + aiosqlite (async)
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, AsyncGenerator

from sqlmodel import Field, SQLModel, create_engine, Session
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from config import settings


# ── Enums ─────────────────────────────────────────────────────────────────────

class CameraStatus(str, Enum):
    online = "online"
    offline = "offline"
    error = "error"

class CameraProtocol(str, Enum):
    rtsp = "rtsp"
    onvif = "onvif"
    http_mjpeg = "http_mjpeg"

class EventType(str, Enum):
    motion = "motion"
    person = "person"
    vehicle = "vehicle"
    intrusion = "intrusion"
    line_crossing = "line_crossing"
    anpr = "anpr"
    face = "face"
    tamper = "tamper"
    connection = "connection"
    disconnection = "disconnection"
    system = "system"

class AlertSeverity(str, Enum):
    info = "info"
    warning = "warning"
    critical = "critical"

class RecordingTrigger(str, Enum):
    motion = "motion"
    event = "event"
    manual = "manual"
    schedule = "schedule"
    continuous = "continuous"


# ── Models ────────────────────────────────────────────────────────────────────

class Camera(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str
    # Connection
    protocol: CameraProtocol = CameraProtocol.onvif
    host: str
    port: int = 80
    rtsp_port: int = 554
    username: str = "admin"
    password: str = ""
    rtsp_path: str = "/stream1"  # override if camera doesn't support ONVIF
    # State
    status: CameraStatus = CameraStatus.offline
    last_seen: Optional[datetime] = None
    # Settings
    enabled: bool = True
    recording_enabled: bool = True
    motion_enabled: bool = True
    armed: bool = True
    resolution: str = "1920x1080"
    fps: int = 15
    # Metadata (populated from ONVIF discovery)
    manufacturer: str = ""
    model: str = ""
    firmware: str = ""
    serial: str = ""
    # Location
    location: str = ""
    notes: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    zones_json: str = Field(default="[]")


class Recording(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    camera_id: str = Field(foreign_key="camera.id")
    filename: str
    filepath: str
    trigger: RecordingTrigger = RecordingTrigger.motion
    started_at: datetime
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    size_bytes: Optional[int] = None
    # AI metadata from camera
    has_person: bool = False
    has_vehicle: bool = False
    has_intrusion: bool = False
    plate_number: str = ""
    thumbnail_path: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Event(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    camera_id: str
    camera_name: str = ""
    event_type: EventType
    object_class: str = ""
    confidence: float = 1.0
    plate_number: str = ""
    zone: str = ""
    raw_payload: str = ""  # JSON string
    recording_id: Optional[str] = None
    snapshot_path: str = ""
    acknowledged: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Alert(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    severity: AlertSeverity = AlertSeverity.info
    title: str
    body: str
    camera_id: Optional[str] = None
    event_id: Optional[str] = None
    read: bool = False
    notified: bool = False  # email/webhook sent
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SystemSetting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ── Engine ────────────────────────────────────────────────────────────────────

DB_URL = f"sqlite+aiosqlite:///{settings.OW_DB_PATH}"
async_engine = create_async_engine(DB_URL, echo=False)
AsyncSessionLocal = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    async with async_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
