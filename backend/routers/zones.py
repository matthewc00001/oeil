# Copyright (c) 2026 Mathieu Cadi — Openema SARL
from __future__ import annotations
import json
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from database import Camera, get_session
from routers.auth import current_user

router = APIRouter()

class ZoneCreate(BaseModel):
    name: str = "Zone 1"
    color: str = "#00e676"
    sensitivity: int = 50
    enabled: bool = True
    points: List[List[float]]

class ZoneUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    sensitivity: Optional[int] = None
    enabled: Optional[bool] = None
    points: Optional[List[List[float]]] = None

class ZoneBulkSave(BaseModel):
    zones: List[dict]

async def _get_camera(camera_id: str, session: AsyncSession) -> Camera:
    result = await session.execute(select(Camera).where(Camera.id == camera_id))
    cam = result.scalars().first()
    if not cam:
        raise HTTPException(404, f"Camera {camera_id} not found")
    return cam

def _load_zones(cam: Camera) -> list:
    raw = getattr(cam, "zones_json", None) or "[]"
    try:
        return json.loads(raw)
    except Exception:
        return []

def _save_zones(cam: Camera, zones: list):
    cam.zones_json = json.dumps(zones)

@router.get("/{camera_id}/zones")
async def list_zones(camera_id: str, session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    cam = await _get_camera(camera_id, session)
    return _load_zones(cam)

@router.post("/{camera_id}/zones", status_code=201)
async def create_zone(camera_id: str, body: ZoneCreate, session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    cam = await _get_camera(camera_id, session)
    zones = _load_zones(cam)
    zone = {"id": str(uuid.uuid4()), "name": body.name, "color": body.color, "sensitivity": body.sensitivity, "enabled": body.enabled, "points": body.points}
    zones.append(zone)
    _save_zones(cam, zones)
    await session.commit()
    return zone

@router.put("/{camera_id}/zones")
async def bulk_save_zones(camera_id: str, body: ZoneBulkSave, session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    cam = await _get_camera(camera_id, session)
    zones = []
    for z in body.zones:
        if not z.get("id"):
            z["id"] = str(uuid.uuid4())
        zones.append(z)
    _save_zones(cam, zones)
    await session.commit()
    return zones

@router.patch("/{camera_id}/zones/{zone_id}")
async def update_zone(camera_id: str, zone_id: str, body: ZoneUpdate, session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    cam = await _get_camera(camera_id, session)
    zones = _load_zones(cam)
    for z in zones:
        if z["id"] == zone_id:
            if body.name is not None: z["name"] = body.name
            if body.color is not None: z["color"] = body.color
            if body.sensitivity is not None: z["sensitivity"] = body.sensitivity
            if body.enabled is not None: z["enabled"] = body.enabled
            if body.points is not None: z["points"] = body.points
            _save_zones(cam, zones)
            await session.commit()
            return z
    raise HTTPException(404, f"Zone {zone_id} not found")

@router.delete("/{camera_id}/zones/{zone_id}", status_code=204)
async def delete_zone(camera_id: str, zone_id: str, session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    cam = await _get_camera(camera_id, session)
    zones = _load_zones(cam)
    new_zones = [z for z in zones if z["id"] != zone_id]
    if len(new_zones) == len(zones):
        raise HTTPException(404, f"Zone {zone_id} not found")
    _save_zones(cam, new_zones)
    await session.commit()

@router.delete("/{camera_id}/zones", status_code=204)
async def delete_all_zones(camera_id: str, session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    cam = await _get_camera(camera_id, session)
    _save_zones(cam, [])
    await session.commit()
