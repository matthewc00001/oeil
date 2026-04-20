# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""
Oeil — Configuration
Reads from environment / /etc/oeil/oeil.env
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="/etc/oeil/oeil.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # API
    OW_HOST: str = "0.0.0.0"
    OW_PORT: int = 8090
    OW_SECRET_KEY: str = "change-me-in-production"
    OW_DEBUG: bool = False

    # Paths
    OW_DATA_DIR: Path = Path("/var/lib/oeil")
    OW_RECORDINGS_DIR: Path = Path("/var/lib/oeil/recordings")
    OW_SNAPSHOTS_DIR: Path = Path("/var/lib/oeil/snapshots")
    OW_DB_PATH: Path = Path("/var/lib/oeil/db/oeil.db")

    # Recording
    OW_RECORD_ON_MOTION: bool = True
    OW_PRE_MOTION_SECONDS: int = 5
    OW_POST_MOTION_SECONDS: int = 15
    OW_MAX_STORAGE_GB: int = 500
    OW_SEGMENT_DURATION: int = 300  # seconds per continuous recording segment

    # go2rtc
    OW_GO2RTC_API: str = "http://127.0.0.1:1984"
    OW_GO2RTC_CONFIG: Path = Path("/etc/oeil/go2rtc.yaml")

    # Cameras config
    OW_CAMERAS_CONFIG: Path = Path("/etc/oeil/cameras.yaml")

    # Notifications
    OW_SMTP_HOST: str = ""
    OW_SMTP_PORT: int = 587
    OW_SMTP_USER: str = ""
    OW_SMTP_PASS: str = ""
    OW_ALERT_EMAIL: str = ""
    OW_WEBHOOK_URL: str = ""
    OW_MQTT_URL: str = ""

    # Auth
    OW_ADMIN_USER: str = "admin"
    OW_ADMIN_PASS: str = "changeme"
    OW_TOKEN_EXPIRE_MINUTES: int = 60 * 8


def _make_settings():
    s = Settings()
    # Decrypt encrypted fields
    for field in ['OW_SMTP_PASS', 'OW_ADMIN_PASS', 'OW_SECRET_KEY']:
        val = getattr(s, field, '')
        if val and str(val).startswith('enc:'):
            object.__setattr__(s, field, _decrypt_if_needed(val))
    return s

settings = _make_settings()
