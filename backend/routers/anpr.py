# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""Oeil — ANPR API Router"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pathlib import Path
from pydantic import BaseModel

from routers.auth import current_user
from services.anpr import ANPRService, PlateDetection, PlateWatchlist

router = APIRouter()


class WatchlistAdd(BaseModel):
    plate_number: str
    tag: str = "flagged"
    notes: str = ""


# ── Detections ─────────────────────────────────────────────────────────────────

@router.get("/detections", response_model=List[dict])
async def list_detections(
    plate: Optional[str] = None,
    camera_id: Optional[str] = None,
    watchlist_only: bool = False,
    days: int = 7,
    limit: int = 100,
    offset: int = 0,
    user=Depends(current_user),
    request: Request = None,
):
    anpr: ANPRService = request.app.state.anpr
    since = datetime.utcnow() - timedelta(days=days)
    detections = await anpr.search_plates(
        plate=plate,
        camera_id=camera_id,
        watchlist_only=watchlist_only,
        since=since,
        limit=limit,
        offset=offset,
    )
    return [d.dict() for d in detections]


@router.get("/detections/{plate}/history")
async def plate_history(
    plate: str,
    days: int = 30,
    user=Depends(current_user),
    request: Request = None,
):
    anpr: ANPRService = request.app.state.anpr
    history = await anpr.get_plate_history(plate, days)
    return [d.dict() for d in history]


@router.get("/detections/{detection_id}/snapshot")
async def detection_snapshot(
    detection_id: str,
    user=Depends(current_user),
    request: Request = None,
):
    from sqlmodel import select
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(PlateDetection).where(PlateDetection.id == detection_id)
        )
        det = result.first()
    if not det or not det.snapshot_path or not Path(det.snapshot_path).exists():
        raise HTTPException(404, "Snapshot not found")
    return FileResponse(det.snapshot_path, media_type="image/jpeg")


@router.get("/stats")
async def anpr_stats(user=Depends(current_user), request: Request = None):
    return await request.app.state.anpr.get_stats()


# ── Watchlist ──────────────────────────────────────────────────────────────────

@router.get("/watchlist", response_model=List[dict])
async def get_watchlist(user=Depends(current_user), request: Request = None):
    anpr: ANPRService = request.app.state.anpr
    entries = await anpr.get_watchlist()
    return [e.dict() for e in entries]


@router.post("/watchlist", status_code=201)
async def add_to_watchlist(
    body: WatchlistAdd,
    user=Depends(current_user),
    request: Request = None,
):
    anpr: ANPRService = request.app.state.anpr
    entry = await anpr.add_to_watchlist(body.plate_number, body.tag, body.notes)
    return entry.dict()


@router.delete("/watchlist/{entry_id}", status_code=204)
async def remove_from_watchlist(
    entry_id: str,
    user=Depends(current_user),
    request: Request = None,
):
    anpr: ANPRService = request.app.state.anpr
    ok = await anpr.remove_from_watchlist(entry_id)
    if not ok:
        raise HTTPException(404, "Watchlist entry not found")
