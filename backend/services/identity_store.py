# Copyright (c) 2026 Mathieu Cadi — Openema SARL
"""
Oeil — Identity Store
Stores known worker body vectors and vehicle color fingerprints.
Resets daily at 7:44AM.
Shared across all cameras.
"""
from __future__ import annotations
import json
import logging
import numpy as np
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("oeil.identity")

STORE_PATH  = Path("/var/lib/oeil/identity")
WORKERS_FILE = STORE_PATH / "known_workers.json"
VEHICLES_FILE = STORE_PATH / "known_vehicles.json"

# Blue Volvo HSV range — owner vehicle, always exempt
# Blue in HSV: hue 100-130, sat>50, val>50
VOLVO_HUE_MIN = 95
VOLVO_HUE_MAX = 135
VOLVO_SAT_MIN = 50
VOLVO_VAL_MIN = 40

# Similarity threshold for body Re-ID (cosine distance)
BODY_MATCH_THRESHOLD = 0.75
# Similarity threshold for vehicle color match
VEHICLE_COLOR_THRESHOLD = 30.0


class IdentityStore:
    def __init__(self):
        STORE_PATH.mkdir(parents=True, exist_ok=True)
        self._known_workers: list[dict]  = []
        self._known_vehicles: list[dict] = []
        self._last_reset_day: int = -1
        self._load()

    def _load(self):
        try:
            if WORKERS_FILE.exists():
                self._known_workers = json.loads(WORKERS_FILE.read_text())
                logger.info(f"Loaded {len(self._known_workers)} known workers")
        except Exception as e:
            logger.error(f"Failed to load workers: {e}")
            self._known_workers = []
        try:
            if VEHICLES_FILE.exists():
                self._known_vehicles = json.loads(VEHICLES_FILE.read_text())
                logger.info(f"Loaded {len(self._known_vehicles)} known vehicles")
        except Exception as e:
            logger.error(f"Failed to load vehicles: {e}")
            self._known_vehicles = []

    def _save_workers(self):
        WORKERS_FILE.write_text(json.dumps(self._known_workers))

    def _save_vehicles(self):
        VEHICLES_FILE.write_text(json.dumps(self._known_vehicles))

    def check_daily_reset(self):
        """Reset known workers daily at 7:44AM."""
        now = datetime.now()
        today = now.weekday() * 10000 + now.hour * 100 + now.minute
        if (now.hour == 7 and now.minute == 44 and
                self._last_reset_day != now.day):
            self._known_workers = []
            self._save_workers()
            self._last_reset_day = now.day
            logger.info("Daily reset: known workers list cleared")

    # ── Blue Volvo detection ──────────────────────────────────────────────────

    def is_blue_volvo(self, vehicle_crop: np.ndarray) -> bool:
        """Check if vehicle crop is predominantly blue (owner's Volvo)."""
        import cv2
        if vehicle_crop is None or vehicle_crop.size == 0:
            return False
        hsv = cv2.cvtColor(vehicle_crop, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(
            hsv,
            np.array([VOLVO_HUE_MIN, VOLVO_SAT_MIN, VOLVO_VAL_MIN]),
            np.array([VOLVO_HUE_MAX, 255, 255])
        )
        blue_ratio = cv2.countNonZero(mask) / (vehicle_crop.shape[0] * vehicle_crop.shape[1])
        return blue_ratio > 0.15  # at least 15% blue pixels

    # ── Vehicle color fingerprint ─────────────────────────────────────────────

    def compute_vehicle_fingerprint(self, vehicle_crop: np.ndarray) -> Optional[list]:
        """Compute color histogram fingerprint of a vehicle."""
        import cv2
        if vehicle_crop is None or vehicle_crop.size == 0:
            return None
        hsv = cv2.cvtColor(vehicle_crop, cv2.COLOR_BGR2HSV)
        hist_h = cv2.calcHist([hsv], [0], None, [18], [0, 180])
        hist_s = cv2.calcHist([hsv], [1], None, [8],  [0, 256])
        hist_v = cv2.calcHist([hsv], [2], None, [8],  [0, 256])
        cv2.normalize(hist_h, hist_h)
        cv2.normalize(hist_s, hist_s)
        cv2.normalize(hist_v, hist_v)
        fingerprint = np.concatenate([
            hist_h.flatten(), hist_s.flatten(), hist_v.flatten()
        ]).tolist()
        return fingerprint

    def is_known_vehicle(self, fingerprint: list) -> bool:
        """Check if vehicle matches any known worker vehicle."""
        if not fingerprint or not self._known_vehicles:
            return False
        fp = np.array(fingerprint)
        for v in self._known_vehicles:
            known_fp = np.array(v['fingerprint'])
            diff = np.linalg.norm(fp - known_fp)
            if diff < VEHICLE_COLOR_THRESHOLD:
                return True
        return False

    def learn_vehicle(self, fingerprint: list, cam_id: str):
        """Save a vehicle fingerprint as known during learning window."""
        if not fingerprint:
            return
        if not self.is_known_vehicle(fingerprint):
            self._known_vehicles.append({
                'fingerprint': fingerprint,
                'cam_id': cam_id,
                'learned_at': datetime.now().isoformat(),
            })
            self._save_vehicles()
            logger.info(f"Learned new worker vehicle from {cam_id}")

    # ── Body Re-ID ────────────────────────────────────────────────────────────

    def compute_body_vector(self, person_crop: np.ndarray) -> Optional[list]:
        """Compute color+shape feature vector for a person."""
        import cv2
        if person_crop is None or person_crop.size == 0:
            return None
        # Resize to standard size
        resized = cv2.resize(person_crop, (64, 128))
        hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
        # Split into upper/lower body
        upper = hsv[:64, :]
        lower = hsv[64:, :]
        def hist(img):
            h = cv2.calcHist([img], [0, 1], None, [16, 8], [0, 180, 0, 256])
            cv2.normalize(h, h)
            return h.flatten()
        vector = np.concatenate([hist(upper), hist(lower)]).tolist()
        return vector

    def is_known_worker(self, vector: list) -> bool:
        """Check if person matches any known worker."""
        if not vector or not self._known_workers:
            return False
        v = np.array(vector)
        for w in self._known_workers:
            known_v = np.array(w['vector'])
            # Cosine similarity
            cos_sim = np.dot(v, known_v) / (
                np.linalg.norm(v) * np.linalg.norm(known_v) + 1e-8)
            if cos_sim > BODY_MATCH_THRESHOLD:
                return True
        return False

    def learn_worker(self, vector: list, cam_id: str):
        """Save a person vector as known worker during learning window."""
        if not vector:
            return
        if not self.is_known_worker(vector):
            self._known_workers.append({
                'vector': vector,
                'cam_id': cam_id,
                'learned_at': datetime.now().isoformat(),
            })
            self._save_workers()
            logger.info(
                f"Learned new worker from {cam_id} "
                f"(total: {len(self._known_workers)})"
            )

    @property
    def worker_count(self) -> int:
        return len(self._known_workers)

    @property
    def vehicle_count(self) -> int:
        return len(self._known_vehicles)
