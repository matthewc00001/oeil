# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""Oeil — Recordings Router"""
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from database import Recording, get_session
from routers.auth import current_user

router = APIRouter()


@router.get("/")
async def list_recordings(
    camera_id: Optional[str] = None,
    has_person: Optional[bool] = None,
    has_vehicle: Optional[bool] = None,
    has_intrusion: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
):
    q = select(Recording).order_by(Recording.started_at.desc()).offset(offset).limit(limit)
    if camera_id:   q = q.where(Recording.camera_id == camera_id)
    if has_person is not None:   q = q.where(Recording.has_person == has_person)
    if has_vehicle is not None:  q = q.where(Recording.has_vehicle == has_vehicle)
    if has_intrusion is not None: q = q.where(Recording.has_intrusion == has_intrusion)
    result = await session.exec(q)
    return result.all()


@router.get("/{rec_id}/download")
async def download(rec_id: str, session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    result = await session.exec(select(Recording).where(Recording.id == rec_id))
    rec = result.first()
    if not rec or not Path(rec.filepath).exists():
        raise HTTPException(404, "Recording file not found")
    return FileResponse(rec.filepath, media_type="video/mp4", filename=rec.filename)


@router.get("/{rec_id}/thumbnail")
async def thumbnail(rec_id: str, session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    result = await session.exec(select(Recording).where(Recording.id == rec_id))
    rec = result.first()
    if not rec or not rec.thumbnail_path or not Path(rec.thumbnail_path).exists():
        raise HTTPException(404, "Thumbnail not found")
    return FileResponse(rec.thumbnail_path, media_type="image/jpeg")


@router.delete("/{rec_id}", status_code=204)
async def delete_recording(rec_id: str, session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    result = await session.exec(select(Recording).where(Recording.id == rec_id))
    rec = result.first()
    if not rec:
        raise HTTPException(404)
    Path(rec.filepath).unlink(missing_ok=True)
    if rec.thumbnail_path:
        Path(rec.thumbnail_path).unlink(missing_ok=True)
    await session.delete(rec)
    await session.commit()
