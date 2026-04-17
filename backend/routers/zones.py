from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_session, MotionZone
from routers.auth import get_current_user
from pydantic import BaseModel
from typing import Optional
import uuid
from datetime import datetime

router = APIRouter(prefix="/zones", tags=["zones"])

class ZoneCreate(BaseModel):
    camera_id: str
    name: str = "Zone 1"
    points: str = "[]"
    enabled: bool = True
    trigger_recording: bool = True
    trigger_alert: bool = True
    sensitivity: int = 50
    color: str = "#00e676"

class ZoneUpdate(BaseModel):
    name: Optional[str] = None
    points: Optional[str] = None
    enabled: Optional[bool] = None
    trigger_recording: Optional[bool] = None
    trigger_alert: Optional[bool] = None
    sensitivity: Optional[int] = None
    color: Optional[str] = None

@router.get("/camera/{camera_id}")
async def get_zones(camera_id: str, session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    result = await session.execute(select(MotionZone).where(MotionZone.camera_id == camera_id))
    return result.scalars().all()

@router.post("/")
async def create_zone(zone: ZoneCreate, session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    db_zone = MotionZone(id=str(uuid.uuid4()), camera_id=zone.camera_id, name=zone.name, points=zone.points, enabled=zone.enabled, trigger_recording=zone.trigger_recording, trigger_alert=zone.trigger_alert, sensitivity=zone.sensitivity, color=zone.color, created_at=datetime.utcnow())
    session.add(db_zone)
    await session.commit()
    await session.refresh(db_zone)
    return db_zone

@router.put("/{zone_id}")
async def update_zone(zone_id: str, zone: ZoneUpdate, session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    result = await session.execute(select(MotionZone).where(MotionZone.id == zone_id))
    db_zone = result.scalar_one_or_none()
    if not db_zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    for k, v in zone.dict(exclude_none=True).items():
        setattr(db_zone, k, v)
    session.add(db_zone)
    await session.commit()
    await session.refresh(db_zone)
    return db_zone

@router.delete("/{zone_id}")
async def delete_zone(zone_id: str, session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    result = await session.execute(select(MotionZone).where(MotionZone.id == zone_id))
    db_zone = result.scalar_one_or_none()
    if not db_zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    await session.delete(db_zone)
    await session.commit()
    return {"ok": True}
