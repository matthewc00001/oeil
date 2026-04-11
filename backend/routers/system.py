# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""Oeil — System Router"""
import shutil
from datetime import datetime
from fastapi import APIRouter, Depends, Request
from sqlmodel import select
from database import Camera, CameraStatus, AsyncSessionLocal
from routers.auth import current_user
from config import settings

router = APIRouter()

@router.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

@router.get("/status")
async def status(user=Depends(current_user), request: Request = None):
    disk = shutil.disk_usage(str(settings.OW_DATA_DIR))
    go2rtc_ok = await request.app.state.go2rtc.is_available()

    async with AsyncSessionLocal() as session:
        result = await session.exec(select(Camera))
        cameras = result.all()

    online = sum(1 for c in cameras if c.status == CameraStatus.online)
    recording = sum(1 for c in cameras if request.app.state.recorder.is_recording(c.id))

    return {
        "version": "1.1.0",
        "go2rtc_available": go2rtc_ok,
        "cameras_total": len(cameras),
        "cameras_online": online,
        "cameras_offline": len(cameras) - online,
        "cameras_recording": recording,
        "disk_total_gb":  round(disk.total / 1024**3, 1),
        "disk_used_gb":   round(disk.used  / 1024**3, 1),
        "disk_free_gb":   round(disk.free  / 1024**3, 1),
        "disk_percent":   round(disk.used  / disk.total * 100, 1),
        "timestamp": datetime.utcnow().isoformat(),
    }

@router.get("/version")
async def version():
    return {"version": "1.1.0", "name": "Oeil"}
