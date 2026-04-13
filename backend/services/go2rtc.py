# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""Oeil — go2rtc REST API client"""
from __future__ import annotations
import logging
import aiohttp

logger = logging.getLogger("oeil.go2rtc")


class Go2RTCClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def add_stream(self, name: str, source: str) -> bool:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.put(
                    f"{self.base_url}/api/streams",
                    json={name: source},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    return r.status < 400
        except Exception as e:
            logger.debug(f"go2rtc add_stream error: {e}")
            return False

    async def remove_stream(self, name: str) -> bool:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.delete(
                    f"{self.base_url}/api/streams",
                    params={"src": name},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    return r.status < 400
        except Exception as e:
            logger.debug(f"go2rtc remove_stream error: {e}")
            return False

    async def get_streams(self) -> dict:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{self.base_url}/api/streams",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    if r.status == 200:
                        return await r.json()
        except Exception:
            pass
        return {}

    async def is_available(self) -> bool:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{self.base_url}/api/streams",
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as r:
                    return r.status == 200
        except Exception:
            return False

    def hls_url(self, name: str) -> str:
        return f"/go2rtc/api/stream.m3u8?src={name}"

    def webrtc_url(self, name: str) -> str:
        return f"/go2rtc/api/webrtc?src={name}"

    def rtsp_url(self, name: str) -> str:
        host = self.base_url.split("://")[-1].split(":")[0]
        return f"rtsp://{host}:8554/{name}"

    def snapshot_url(self, name: str) -> str:
        return f"/go2rtc/api/frame.jpeg?src={name}"
