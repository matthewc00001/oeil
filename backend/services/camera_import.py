# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""
Oeil — Camera YAML Import Service
Reads /etc/oeil/cameras.yaml and syncs cameras into the database.
Called at startup and via CLI: oeil-cli import-cameras
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

import yaml
from sqlmodel import select

from database import Camera, CameraProtocol, AsyncSessionLocal
from config import settings

logger = logging.getLogger("oeil.import")


async def import_cameras_from_yaml(yaml_path: Path = None) -> dict:
    """
    Import cameras from YAML config into database.
    Skips cameras whose host already exists (no duplicates).
    Returns counts of created/skipped.
    """
    path = yaml_path or settings.OW_CAMERAS_CONFIG
    if not path.exists():
        logger.warning(f"Camera config not found: {path}")
        return {"created": 0, "skipped": 0, "errors": 0}

    with open(path) as f:
        data = yaml.safe_load(f)

    cameras_raw: List[dict] = data.get("cameras", [])
    created = skipped = errors = 0

    async with AsyncSessionLocal() as session:
        for raw in cameras_raw:
            try:
                host = raw.get("host", "").strip()
                if not host:
                    logger.warning(f"Camera entry missing host: {raw}")
                    errors += 1
                    continue

                # Skip if already exists (match by host)
                existing = await session.exec(
                    select(Camera).where(Camera.host == host)
                )
                if existing.first():
                    logger.debug(f"Camera already exists: {host} — skipping")
                    skipped += 1
                    continue

                protocol_str = raw.get("protocol", "onvif").lower()
                try:
                    protocol = CameraProtocol(protocol_str)
                except ValueError:
                    protocol = CameraProtocol.onvif

                cam = Camera(
                    name=raw.get("name", f"Camera {host}"),
                    protocol=protocol,
                    host=host,
                    port=int(raw.get("port", 80)),
                    rtsp_port=int(raw.get("rtsp_port", 554)),
                    username=raw.get("username", "admin"),
                    password=raw.get("password", ""),
                    rtsp_path=raw.get("rtsp_path", "/stream1"),
                    enabled=bool(raw.get("enabled", True)),
                    recording_enabled=bool(raw.get("recording_enabled", True)),
                    motion_enabled=bool(raw.get("motion_enabled", True)),
                    armed=bool(raw.get("armed", True)),
                    resolution=raw.get("resolution", "1920x1080"),
                    fps=int(raw.get("fps", 15)),
                    location=raw.get("location", ""),
                    notes=raw.get("notes", ""),
                )
                session.add(cam)
                created += 1
                logger.info(f"Imported camera: {cam.name} ({host})")

            except Exception as e:
                logger.error(f"Error importing camera {raw}: {e}")
                errors += 1

        await session.commit()

    result = {"created": created, "skipped": skipped, "errors": errors}
    logger.info(f"Camera import complete: {result}")
    return result


async def export_cameras_to_yaml(yaml_path: Path = None) -> str:
    """Export all cameras from DB back to YAML format."""
    path = yaml_path or settings.OW_CAMERAS_CONFIG
    async with AsyncSessionLocal() as session:
        result = await session.exec(select(Camera))
        cameras = result.all()

    output = {"cameras": []}
    for c in cameras:
        output["cameras"].append({
            "name": c.name,
            "protocol": c.protocol.value,
            "host": c.host,
            "port": c.port,
            "rtsp_port": c.rtsp_port,
            "username": c.username,
            "password": c.password,
            "rtsp_path": c.rtsp_path,
            "enabled": c.enabled,
            "recording_enabled": c.recording_enabled,
            "motion_enabled": c.motion_enabled,
            "armed": c.armed,
            "resolution": c.resolution,
            "fps": c.fps,
            "location": c.location,
            "notes": c.notes,
        })

    yaml_str = yaml.dump(output, default_flow_style=False, allow_unicode=True)
    if path:
        path.write_text(yaml_str)
        logger.info(f"Cameras exported to {path}")
    return yaml_str
