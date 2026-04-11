# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""Oeil — Streams Router"""
from fastapi import APIRouter, Depends, Request
from routers.auth import current_user

router = APIRouter()

@router.get("/")
async def list_streams(user=Depends(current_user), request: Request = None):
    return await request.app.state.go2rtc.get_streams()

@router.get("/{camera_id}/urls")
async def stream_urls(camera_id: str, user=Depends(current_user), request: Request = None):
    g = request.app.state.go2rtc
    return {
        "hls":      g.hls_url(camera_id),
        "webrtc":   g.webrtc_url(camera_id),
        "rtsp":     g.rtsp_url(camera_id),
        "snapshot": g.snapshot_url(camera_id),
    }

@router.get("/go2rtc/status")
async def go2rtc_status(user=Depends(current_user), request: Request = None):
    ok = await request.app.state.go2rtc.is_available()
    return {"available": ok}
