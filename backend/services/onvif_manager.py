# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""
Oeil — ONVIF Manager
Discovers Ability Enterprise cameras on the network, pulls stream URIs,
subscribes to ONVIF events (motion, intrusion, person, vehicle, ANPR)
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Optional

import aiohttp
from wsdiscovery import WSDiscovery
from onvif import ONVIFCamera

from database import Camera, CameraProtocol, CameraStatus, Event, EventType, AsyncSessionLocal
from services.event_bus import EventBus
from config import Settings

logger = logging.getLogger("oeil.onvif")


class CameraConnection:
    """Holds live state for one connected camera."""

    def __init__(self, camera: Camera):
        self.camera = camera
        self.onvif_cam: Optional[ONVIFCamera] = None
        self.rtsp_uri: str = ""
        self.subscription_task: Optional[asyncio.Task] = None
        self.health_task: Optional[asyncio.Task] = None
        self.online: bool = False


class ONVIFManager:
    def __init__(self, settings: Settings, event_bus: EventBus):
        self.settings = settings
        self.event_bus = event_bus
        self._connections: Dict[str, CameraConnection] = {}
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self):
        self._running = True
        self._tasks.append(asyncio.create_task(self._load_cameras()))
        self._tasks.append(asyncio.create_task(self._discovery_loop()))
        logger.info("ONVIF manager started")

    async def stop(self):
        self._running = False
        for t in self._tasks:
            t.cancel()
        for conn in self._connections.values():
            if conn.subscription_task:
                conn.subscription_task.cancel()
            if conn.health_task:
                conn.health_task.cancel()

    # ── Camera loading ─────────────────────────────────────────────────────────

    async def _load_cameras(self):
        """Load cameras from DB and connect to each."""
        async with AsyncSessionLocal() as session:
            from sqlmodel import select
            result = await session.exec(select(Camera).where(Camera.enabled == True))
            cameras = result.all()

        for cam in cameras:
            await self._connect_camera(cam)

    async def _connect_camera(self, camera: Camera):
        logger.info(f"Connecting to camera: {camera.name} ({camera.host})")
        conn = CameraConnection(camera)
        self._connections[camera.id] = conn

        try:
            if camera.protocol == CameraProtocol.onvif:
                await self._setup_onvif(conn)
            else:
                # Direct RTSP — no ONVIF
                conn.rtsp_uri = f"rtsp://{camera.username}:{camera.password}@{camera.host}:{camera.rtsp_port}{camera.rtsp_path}"
                conn.online = True

            await self._register_with_go2rtc(conn)
            await self._update_camera_status(camera.id, CameraStatus.online)

            # Subscribe to ONVIF events
            if camera.protocol == CameraProtocol.onvif and conn.onvif_cam:
                conn.subscription_task = asyncio.create_task(
                    self._onvif_event_loop(conn)
                )

            conn.health_task = asyncio.create_task(self._health_check(conn))

        except Exception as e:
            logger.error(f"Failed to connect to {camera.name}: {e}")
            await self._update_camera_status(camera.id, CameraStatus.error)

    async def _setup_onvif(self, conn: CameraConnection):
        cam = conn.camera
        try:
            onvif_cam = ONVIFCamera(cam.host, cam.port, cam.username, cam.password)
            await asyncio.get_event_loop().run_in_executor(None, onvif_cam.update_xaddrs)

            media_service = onvif_cam.create_media_service()
            profiles = await asyncio.get_event_loop().run_in_executor(None, media_service.GetProfiles)

            if profiles:
                token = profiles[0].token
                uri_req = media_service.create_type("GetStreamUri")
                uri_req.ProfileToken = token
                uri_req.StreamSetup = {
                    "Stream": "RTP-Unicast",
                    "Transport": {"Protocol": "RTSP"},
                }
                uri_resp = await asyncio.get_event_loop().run_in_executor(
                    None, media_service.GetStreamUri, uri_req
                )
                raw_uri = uri_resp.Uri
                # Inject credentials into URI
                host = cam.host
                conn.rtsp_uri = raw_uri.replace(
                    "rtsp://", f"rtsp://{cam.username}:{cam.password}@"
                ) if "@" not in raw_uri else raw_uri

            # Pull device info
            device_service = onvif_cam.create_devicemgmt_service()
            info = await asyncio.get_event_loop().run_in_executor(
                None, device_service.GetDeviceInformation
            )
            async with AsyncSessionLocal() as session:
                from sqlmodel import select
                result = await session.exec(select(Camera).where(Camera.id == cam.id))
                db_cam = result.first()
                if db_cam:
                    db_cam.manufacturer = getattr(info, "Manufacturer", "")
                    db_cam.model = getattr(info, "Model", "")
                    db_cam.firmware = getattr(info, "FirmwareVersion", "")
                    db_cam.serial = getattr(info, "SerialNumber", "")
                    await session.commit()

            conn.onvif_cam = onvif_cam
            conn.online = True
            logger.info(f"ONVIF connected: {cam.name} → {conn.rtsp_uri}")

        except Exception as e:
            logger.warning(f"ONVIF setup failed for {cam.name}: {e} — falling back to direct RTSP")
            conn.rtsp_uri = f"rtsp://{cam.username}:{cam.password}@{cam.host}:{cam.rtsp_port}{cam.rtsp_path}"
            conn.online = True

    # ── go2rtc registration ────────────────────────────────────────────────────

    async def _register_with_go2rtc(self, conn: CameraConnection):
        """Add/update this camera's stream in go2rtc via its API."""
        if not conn.rtsp_uri:
            return
        cam_id = conn.camera.id
        url = f"{self.settings.OW_GO2RTC_API}/api/streams"
        payload = {cam_id: conn.rtsp_uri}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status in (200, 201, 204):
                        logger.info(f"Registered {conn.camera.name} with go2rtc")
                    else:
                        logger.warning(f"go2rtc registration returned {resp.status}")
        except Exception as e:
            logger.warning(f"Could not register with go2rtc: {e}")

    # ── ONVIF event subscription ───────────────────────────────────────────────

    async def _onvif_event_loop(self, conn: CameraConnection):
        """
        Subscribe to ONVIF PullPoint events from Ability cameras.
        Ability cameras emit: RuleEngine/FieldDetector/ObjectsInside (intrusion),
        RuleEngine/LineDetector/Crossed (trip wire), VideoSource/MotionAlarm,
        and proprietary AI events.
        """
        cam = conn.camera
        while self._running and conn.online:
            try:
                onvif_cam = conn.onvif_cam
                event_service = onvif_cam.create_events_service()

                # Create PullPoint subscription
                sub = await asyncio.get_event_loop().run_in_executor(
                    None, event_service.CreatePullPointSubscription, {}
                )

                logger.info(f"ONVIF event subscription active: {cam.name}")

                while self._running and conn.online:
                    try:
                        pull = await asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: event_service.PullMessages({
                                "MessageLimit": 10,
                                "Timeout": "PT10S",
                            })
                        )
                        for msg in getattr(pull, "NotificationMessage", []):
                            await self._handle_onvif_event(cam, msg)
                    except Exception as e:
                        logger.debug(f"PullMessages error ({cam.name}): {e}")
                        await asyncio.sleep(2)

            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(f"ONVIF event loop error ({cam.name}): {e}")
                await asyncio.sleep(10)

    async def _handle_onvif_event(self, camera: Camera, msg):
        """Parse ONVIF notification message and publish to event bus."""
        try:
            topic = str(getattr(msg, "Topic", ""))
            data = str(msg)

            # Map ONVIF topics to Oeil event types
            event_type = EventType.motion
            object_class = ""

            if "FieldDetector" in topic or "ObjectsInside" in topic:
                event_type = EventType.intrusion
            elif "LineDetector" in topic or "Crossed" in topic:
                event_type = EventType.line_crossing
            elif "MotionAlarm" in topic:
                event_type = EventType.motion
            elif "Person" in data or "person" in data:
                event_type = EventType.person
                object_class = "person"
            elif "Vehicle" in data or "vehicle" in data:
                event_type = EventType.vehicle
                object_class = "vehicle"
            elif "ANPR" in topic or "PlateNumber" in data:
                event_type = EventType.anpr
                object_class = "license_plate"
            elif "Tamper" in topic:
                event_type = EventType.tamper

            event = {
                "type": "camera_event",
                "camera_id": camera.id,
                "camera_name": camera.name,
                "event_type": event_type.value,
                "object_class": object_class,
                "confidence": 0.9,
                "topic": topic,
                "timestamp": datetime.utcnow().isoformat(),
            }

            await self.event_bus.publish(event)
            await self._persist_event(camera, event_type, object_class, topic)

        except Exception as e:
            logger.debug(f"Failed to parse ONVIF event: {e}")

    async def _persist_event(self, camera: Camera, event_type: EventType, object_class: str, raw: str):
        async with AsyncSessionLocal() as session:
            ev = Event(
                camera_id=camera.id,
                camera_name=camera.name,
                event_type=event_type,
                object_class=object_class,
                confidence=0.9,
                raw_payload=raw[:2000],
            )
            session.add(ev)
            await session.commit()

    # ── Health monitoring ──────────────────────────────────────────────────────

    async def _health_check(self, conn: CameraConnection):
        """Ping camera every 30s; update DB status on change."""
        while self._running:
            await asyncio.sleep(30)
            was_online = conn.online
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"http://{conn.camera.host}/",
                        timeout=aiohttp.ClientTimeout(total=3)
                    ) as resp:
                        conn.online = resp.status < 500
            except Exception:
                conn.online = False

            if conn.online != was_online:
                status = CameraStatus.online if conn.online else CameraStatus.offline
                await self._update_camera_status(conn.camera.id, status)
                await self.event_bus.publish({
                    "type": "camera_status",
                    "camera_id": conn.camera.id,
                    "camera_name": conn.camera.name,
                    "status": status.value,
                    "timestamp": datetime.utcnow().isoformat(),
                })
                logger.info(f"Camera {conn.camera.name} is now {status.value}")

    async def _update_camera_status(self, camera_id: str, status: CameraStatus):
        async with AsyncSessionLocal() as session:
            from sqlmodel import select
            result = await session.exec(select(Camera).where(Camera.id == camera_id))
            cam = result.first()
            if cam:
                cam.status = status
                cam.last_seen = datetime.utcnow() if status == CameraStatus.online else cam.last_seen
                await session.commit()

    # ── WS-Discovery ──────────────────────────────────────────────────────────

    async def _discovery_loop(self):
        """Periodically scan network for new ONVIF cameras."""
        await asyncio.sleep(30)  # wait for startup
        while self._running:
            try:
                await asyncio.get_event_loop().run_in_executor(None, self._discover_cameras)
            except Exception as e:
                logger.debug(f"Discovery error: {e}")
            await asyncio.sleep(300)  # every 5 minutes

    def _discover_cameras(self):
        try:
            wsd = WSDiscovery()
            wsd.start()
            services = wsd.searchServices(types=["NetworkVideoTransmitter"])
            wsd.stop()
            for svc in services:
                logger.info(f"Discovered ONVIF device: {svc.getXAddrs()}")
        except Exception as e:
            logger.debug(f"WS-Discovery failed: {e}")

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_rtsp_uri(self, camera_id: str) -> Optional[str]:
        conn = self._connections.get(camera_id)
        return conn.rtsp_uri if conn else None

    async def add_camera(self, camera: Camera):
        await self._connect_camera(camera)

    async def remove_camera(self, camera_id: str):
        conn = self._connections.pop(camera_id, None)
        if conn:
            if conn.subscription_task:
                conn.subscription_task.cancel()
            if conn.health_task:
                conn.health_task.cancel()
