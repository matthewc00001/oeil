# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""Oeil — Events Router"""
from typing import Optional
from fastapi import APIRouter, Depends
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from database import Event, get_session
from routers.auth import current_user

router = APIRouter()

@router.get("/")
async def list_events(
    camera_id: Optional[str] = None,
    event_type: Optional[str] = None,
    acknowledged: Optional[bool] = None,
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
):
    q = select(Event).order_by(Event.created_at.desc()).offset(offset).limit(limit)
    if camera_id:   q = q.where(Event.camera_id == camera_id)
    if event_type:  q = q.where(Event.event_type == event_type)
    if acknowledged is not None: q = q.where(Event.acknowledged == acknowledged)
    result = await session.exec(q)
    return result.all()

@router.post("/{event_id}/acknowledge")
async def ack(event_id: str, session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    result = await session.exec(select(Event).where(Event.id == event_id))
    ev = result.first()
    if ev:
        ev.acknowledged = True
        await session.commit()
    return {"acknowledged": True}

@router.post("/acknowledge-all")
async def ack_all(session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    result = await session.exec(select(Event).where(Event.acknowledged == False))
    for ev in result.all():
        ev.acknowledged = True
    await session.commit()
    return {"ok": True}
