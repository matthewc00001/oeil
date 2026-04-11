# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""Oeil — Schedules Router"""
from typing import List
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from routers.auth import current_user

router = APIRouter()

class ScheduleRule(BaseModel):
    name: str
    days: List[int]          # 0=Mon … 6=Sun
    arm_time: str            # "HH:MM"
    disarm_time: str         # "HH:MM"
    camera_ids: List[str]    # ["all"] or list of camera UUIDs

@router.get("/")
async def get_schedules(user=Depends(current_user), request: Request = None):
    return await request.app.state.scheduler.get_rules()

@router.put("/")
async def save_schedules(
    rules: List[ScheduleRule],
    user=Depends(current_user),
    request: Request = None,
):
    await request.app.state.scheduler.save_rules([r.dict() for r in rules])
    return {"saved": len(rules)}
