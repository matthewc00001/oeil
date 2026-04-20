# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""
Oeil — Main FastAPI Application (v2)
Integrates: ONVIF, Recorder, ANPR, Snapshots, Scheduler, Camera Import
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from config import settings
from database import init_db, get_session
from routers import cameras, recordings, events, alerts, auth, system, streams, zones
from services.onvif_manager import ONVIFManager
from services.recorder import RecorderService
from services.event_bus import EventBus
from services.go2rtc import Go2RTCClient
from services.notification import NotificationService
from services.motion_detector import MotionDetectorService
from services.ai_detector import AIDetectorService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("oeil")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("Oeil starting up…")

    await init_db()

    # Start core services
    app.state.event_bus = EventBus()
    app.state.go2rtc = Go2RTCClient(settings.OW_GO2RTC_API)
    app.state.notifications = NotificationService(settings)
    app.state.onvif = ONVIFManager(settings, app.state.event_bus)
    app.state.recorder = RecorderService(settings, app.state.event_bus)

    await app.state.onvif.start()
    await app.state.recorder.start()
    app.state.motion = MotionDetectorService(settings, app.state.event_bus)
    await app.state.motion.start()
    app.state.ai = AIDetectorService(settings, app.state.event_bus)
    await app.state.ai.start()

    logger.info("All services started — Oeil ready")
    yield

    logger.info("Oeil shutting down…")
    await app.state.ai.stop()
    await app.state.motion.stop()
    await app.state.recorder.stop()
    await app.state.onvif.stop()


app = FastAPI(
    title="Oeil API",
    version="1.0.0",
    description="Open-source backend for Ability Enterprise AI edge cameras",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router,       prefix="/api/auth",       tags=["auth"])
app.include_router(cameras.router,    prefix="/api/cameras",    tags=["cameras"])
app.include_router(recordings.router, prefix="/api/recordings", tags=["recordings"])
app.include_router(events.router,     prefix="/api/events",     tags=["events"])
app.include_router(alerts.router,     prefix="/api/alerts",     tags=["alerts"])
app.include_router(streams.router,    prefix="/api/streams",    tags=["streams"])
app.include_router(system.router,     prefix="/api/system",     tags=["system"])
app.include_router(zones.router,     prefix="/api/cameras",   tags=["zones"])


# ── WebSocket: live event push ────────────────────────────────────────────────
@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    await websocket.accept()
    bus: EventBus = app.state.event_bus
    queue = asyncio.Queue(maxsize=100)
    bus.subscribe(queue)
    try:
        while True:
            msg = await queue.get()
            await websocket.send_json(msg)
    except WebSocketDisconnect:
        bus.unsubscribe(queue)
    except Exception:
        bus.unsubscribe(queue)


# ── Camera event webhook (Ability cameras push HTTP events) ───────────────────
@app.post("/api/webhook/camera-event")
async def camera_webhook(payload: dict):
    """
    Ability Enterprise cameras POST JSON events here.
    Configure camera's HTTP notification URL to:
        http://<server>:8090/api/webhook/camera-event
    """
    bus: EventBus = app.state.event_bus
    recorder: RecorderService = app.state.recorder
    notif: NotificationService = app.state.notifications

    camera_id = payload.get("camera_id") or payload.get("device_id") or "unknown"
    event_type = payload.get("event_type", "unknown")
    confidence = payload.get("confidence", 1.0)
    object_class = payload.get("class") or payload.get("object_class", "")

    event = {
        "type": "camera_event",
        "camera_id": camera_id,
        "event_type": event_type,
        "object_class": object_class,
        "confidence": confidence,
        "raw": payload,
    }

    await bus.publish(event)

    # Trigger recording on motion/intrusion events
    if event_type in ("motion", "intrusion", "person", "vehicle", "line_crossing"):
        await recorder.trigger_event_recording(camera_id, event_type)

    # Send notifications for high-confidence detections
    if confidence >= 0.7 and event_type in ("intrusion", "person", "vehicle"):
        await notif.send_alert(
            title=f"{event_type.title()} detected — Camera {camera_id}",
            body=f"Object: {object_class} | Confidence: {confidence:.0%}",
            camera_id=camera_id,
        )

    return {"status": "ok"}


# ── Serve frontend ────────────────────────────────────────────────────────────
FRONTEND_DIR = Path(__file__).parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        index = FRONTEND_DIR / "index.html"
        return FileResponse(str(index))
