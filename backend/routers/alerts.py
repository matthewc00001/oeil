# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""Oeil — Alerts Router"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from database import Alert, AlertSeverity, get_session
from routers.auth import current_user

router = APIRouter()

class AlertCreate(BaseModel):
    severity: AlertSeverity = AlertSeverity.info
    title: str
    body: str
    camera_id: Optional[str] = None

@router.get("/")
async def list_alerts(
    unread_only: bool = False,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
):
    q = select(Alert).order_by(Alert.created_at.desc()).limit(limit)
    if unread_only:
        q = q.where(Alert.read == False)
    result = await session.exec(q)
    return result.all()

@router.post("/", status_code=201)
async def create_alert(body: AlertCreate, session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    alert = Alert(**body.dict())
    session.add(alert)
    await session.commit()
    await session.refresh(alert)
    return alert

@router.post("/{alert_id}/read")
async def mark_read(alert_id: str, session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    result = await session.exec(select(Alert).where(Alert.id == alert_id))
    al = result.first()
    if not al:
        raise HTTPException(404)
    al.read = True
    await session.commit()
    return {"read": True}

@router.post("/read-all")
async def read_all(session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    result = await session.exec(select(Alert).where(Alert.read == False))
    for al in result.all():
        al.read = True
    await session.commit()
    return {"ok": True}

@router.delete("/{alert_id}", status_code=204)
async def delete_alert(alert_id: str, session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    result = await session.exec(select(Alert).where(Alert.id == alert_id))
    al = result.first()
    if not al:
        raise HTTPException(404)
    await session.delete(al)
    await session.commit()
