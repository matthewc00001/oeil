# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""Oeil — Cameras Router"""
from __future__ import annotations
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Camera, CameraProtocol, CameraStatus, get_session
from routers.auth import current_user

router = APIRouter()


class CameraCreate(BaseModel):
    name: str
    protocol: CameraProtocol = CameraProtocol.onvif
    host: str
    port: int = 80
    rtsp_port: int = 554
    username: str = "admin"
    password: str = ""
    rtsp_path: str = "/stream1"
    enabled: bool = True
    recording_enabled: bool = True
    motion_enabled: bool = True
    armed: bool = True
    resolution: str = "1920x1080"
    fps: int = 15
    location: str = ""
    notes: str = ""


class CameraUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    recording_enabled: Optional[bool] = None
    motion_enabled: Optional[bool] = None
    armed: Optional[bool] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    password: Optional[str] = None


@router.get("/")
async def list_cameras(
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
    request: Request = None,
):
    result = await session.exec(select(Camera))
    cameras = result.all()
    go2rtc = request.app.state.go2rtc
    recorder = request.app.state.recorder
    out = []
    for c in cameras:
        d = c.dict()
        d["hls_url"]      = go2rtc.hls_url(c.id)
        d["webrtc_url"]   = go2rtc.webrtc_url(c.id)
        d["snapshot_url"] = go2rtc.snapshot_url(c.id)
        d["rtsp_url"]     = go2rtc.rtsp_url(c.id)
        d["is_recording"] = recorder.is_recording(c.id)
        # Latest snapshot from disk
        snap = request.app.state.snapshots.latest_snapshot(c.id)
        d["latest_snapshot"] = f"/snapshots/{c.id}/{snap.name}" if snap else None
        out.append(d)
    return out


@router.post("/", status_code=201)
async def create_camera(
    body: CameraCreate,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
    request: Request = None,
):
    cam = Camera(**body.dict())
    session.add(cam)
    await session.commit()
    await session.refresh(cam)
    await request.app.state.onvif.add_camera(cam)
    return cam


@router.get("/{camera_id}")
async def get_camera(
    camera_id: str,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
):
    result = await session.exec(select(Camera).where(Camera.id == camera_id))
    cam = result.first()
    if not cam:
        raise HTTPException(404, "Camera not found")
    return cam


@router.patch("/{camera_id}")
async def update_camera(
    camera_id: str,
    body: CameraUpdate,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
):
    result = await session.exec(select(Camera).where(Camera.id == camera_id))
    cam = result.first()
    if not cam:
        raise HTTPException(404, "Camera not found")
    for k, v in body.dict(exclude_none=True).items():
        setattr(cam, k, v)
    await session.commit()
    return cam


@router.delete("/{camera_id}", status_code=204)
async def delete_camera(
    camera_id: str,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
    request: Request = None,
):
    result = await session.exec(select(Camera).where(Camera.id == camera_id))
    cam = result.first()
    if not cam:
        raise HTTPException(404, "Camera not found")
    await request.app.state.onvif.remove_camera(camera_id)
    await session.delete(cam)
    await session.commit()


@router.post("/{camera_id}/arm")
async def arm(camera_id: str, session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    result = await session.exec(select(Camera).where(Camera.id == camera_id))
    cam = result.first()
    if cam:
        cam.armed = True
        await session.commit()
    return {"armed": True}


@router.post("/{camera_id}/disarm")
async def disarm(camera_id: str, session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    result = await session.exec(select(Camera).where(Camera.id == camera_id))
    cam = result.first()
    if cam:
        cam.armed = False
        await session.commit()
    return {"armed": False}


@router.post("/{camera_id}/snapshot")
async def take_snapshot(camera_id: str, user=Depends(current_user), request: Request = None):
    path = await request.app.state.snapshots.capture(camera_id)
    if not path:
        raise HTTPException(503, "Snapshot failed — check camera connectivity")
    return {"snapshot": f"/snapshots/{camera_id}/{path.name}"}


@router.post("/{camera_id}/record/start")
async def start_rec(camera_id: str, user=Depends(current_user), request: Request = None):
    ok = await request.app.state.recorder.start_manual(camera_id)
    return {"recording": ok}


@router.post("/{camera_id}/record/stop")
async def stop_rec(camera_id: str, user=Depends(current_user), request: Request = None):
    ok = await request.app.state.recorder.stop_manual(camera_id)
    return {"recording": not ok}


@router.post("/arm-all")
async def arm_all(session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    result = await session.exec(select(Camera))
    for cam in result.all():
        cam.armed = True
    await session.commit()
    return {"armed": True}


@router.post("/disarm-all")
async def disarm_all(session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    result = await session.exec(select(Camera))
    for cam in result.all():
        cam.armed = False
    await session.commit()
    return {"armed": False}


@router.post("/import-yaml")
async def import_from_yaml(user=Depends(current_user), request: Request = None):
    from services.camera_import import import_cameras_from_yaml
    result = await import_cameras_from_yaml()
    return result


@router.get("/export-yaml", response_class=__import__("fastapi").responses.PlainTextResponse)
async def export_yaml(user=Depends(current_user)):
    from services.camera_import import export_cameras_to_yaml
    yaml_str = await export_cameras_to_yaml(yaml_path=None)
    return yaml_str
