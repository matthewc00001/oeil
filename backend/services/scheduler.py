# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""
Oeil — Schedule Service
Time-based arming/disarming of cameras and scheduled recording.
Rules stored in DB, evaluated every minute.

Example rules (stored as JSON in SystemSetting):
  [
    {"name": "Night arm", "days": [0,1,2,3,4,5,6], "arm_time": "22:00", "disarm_time": "07:00",
     "camera_ids": ["all"]},
    {"name": "Weekday only", "days": [0,1,2,3,4], "arm_time": "08:00", "disarm_time": "18:00",
     "camera_ids": ["cam-uuid-1", "cam-uuid-2"]}
  ]
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, time
from typing import List, Optional

from sqlmodel import select

from database import Camera, SystemSetting, AsyncSessionLocal

logger = logging.getLogger("oeil.scheduler")


class ScheduleService:
    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Schedule service started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self):
        while self._running:
            try:
                await self._evaluate_rules()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Schedule evaluation error: {e}")
            await asyncio.sleep(60)

    async def _evaluate_rules(self):
        rules = await self._get_rules()
        if not rules:
            return

        now = datetime.now()
        current_day = now.weekday()   # 0=Mon … 6=Sun
        current_time = now.time().replace(second=0, microsecond=0)

        async with AsyncSessionLocal() as session:
            for rule in rules:
                days: List[int] = rule.get("days", list(range(7)))
                if current_day not in days:
                    continue

                arm_str = rule.get("arm_time", "")
                disarm_str = rule.get("disarm_time", "")
                camera_ids: List[str] = rule.get("camera_ids", ["all"])

                should_arm: Optional[bool] = None

                if arm_str and disarm_str:
                    arm_t = _parse_time(arm_str)
                    disarm_t = _parse_time(disarm_str)
                    if arm_t and disarm_t:
                        # Handle overnight schedules (e.g. 22:00 → 07:00)
                        if arm_t > disarm_t:
                            should_arm = current_time >= arm_t or current_time < disarm_t
                        else:
                            should_arm = arm_t <= current_time < disarm_t

                if should_arm is None:
                    continue

                # Apply to cameras
                q = select(Camera)
                if "all" not in camera_ids:
                    q = q.where(Camera.id.in_(camera_ids))

                result = await session.exec(q)
                cameras = result.all()
                changed = 0
                for cam in cameras:
                    if cam.armed != should_arm:
                        cam.armed = should_arm
                        changed += 1

                if changed:
                    await session.commit()
                    state = "ARMED" if should_arm else "DISARMED"
                    logger.info(f"Schedule '{rule.get('name')}': {changed} cameras → {state}")

    async def _get_rules(self) -> List[dict]:
        async with AsyncSessionLocal() as session:
            result = await session.exec(
                select(SystemSetting).where(SystemSetting.key == "schedules")
            )
            setting = result.first()
            if not setting:
                return []
            try:
                return json.loads(setting.value)
            except Exception:
                return []

    async def save_rules(self, rules: List[dict]):
        async with AsyncSessionLocal() as session:
            result = await session.exec(
                select(SystemSetting).where(SystemSetting.key == "schedules")
            )
            setting = result.first()
            if setting:
                setting.value = json.dumps(rules)
                setting.updated_at = datetime.utcnow()
            else:
                setting = SystemSetting(key="schedules", value=json.dumps(rules))
                session.add(setting)
            await session.commit()
        logger.info(f"Saved {len(rules)} schedule rules")

    async def get_rules(self) -> List[dict]:
        return await self._get_rules()


def _parse_time(t_str: str) -> Optional[time]:
    try:
        parts = t_str.split(":")
        return time(int(parts[0]), int(parts[1]))
    except Exception:
        return None
