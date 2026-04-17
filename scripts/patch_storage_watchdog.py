#!/usr/bin/env python3
"""
Oeil — Storage Watchdog Patch
Replaces _storage_watchdog in /opt/oeil/services/recorder.py

Rules:
  1. Delete recordings older than 36 hours (always)
  2. Never exceed 50% of total disk space (delete oldest first)

Run on Monday:
  python3 /tmp/patch_storage_watchdog.py
  systemctl kill -s SIGKILL oeil-api && sleep 3 && systemctl start oeil-api
  journalctl -u oeil-api -n 20 --no-pager | grep -i storage
"""

RECORDER_PATH = "/opt/oeil/services/recorder.py"

OLD_WATCHDOG = '''    async def _storage_watchdog(self):
        """Delete oldest recordings when storage limit is reached."""
        while self._running:
            await asyncio.sleep(3600)  # check hourly
            try:
                rec_dir = self.settings.OW_RECORDINGS_DIR
                if not rec_dir.exists():
                    continue
                total_bytes = sum(
                    f.stat().st_size for f in rec_dir.rglob("*.mp4") if f.is_file()
                )
                max_bytes = self.settings.OW_MAX_STORAGE_GB * 1024 ** 3
                if total_bytes > max_bytes:
                    files = sorted(rec_dir.rglob("*.mp4"), key=lambda f: f.stat().st_mtime)
                    for f in files:
                        if total_bytes <= max_bytes * 0.9:
                            break
                        total_bytes -= f.stat().st_size
                        f.unlink()
                        logger.info(f"Storage cleanup: deleted {f.name}")
            except Exception as e:
                logger.error(f"Storage watchdog error: {e}")'''

NEW_WATCHDOG = '''    async def _storage_watchdog(self):
        """
        Storage watchdog — runs every hour.
        Rule 1: Delete all recordings older than 36 hours.
        Rule 2: If disk usage > 50%, delete oldest recordings until back under 50%.
        """
        import shutil
        import time

        MAX_AGE_SECONDS = 36 * 3600   # 36 hours
        MAX_DISK_PERCENT = 50.0        # never exceed 50% of total disk

        while self._running:
            await asyncio.sleep(3600)  # check every hour
            try:
                rec_dir = self.settings.OW_RECORDINGS_DIR
                if not rec_dir.exists():
                    continue

                now = time.time()
                deleted_age  = 0
                deleted_size = 0

                # ── Rule 1: Delete recordings older than 36 hours ──────────
                all_files = sorted(
                    [f for f in rec_dir.rglob("*.mp4") if f.is_file()],
                    key=lambda f: f.stat().st_mtime
                )
                for f in all_files:
                    age_seconds = now - f.stat().st_mtime
                    if age_seconds > MAX_AGE_SECONDS:
                        size = f.stat().st_size
                        f.unlink(missing_ok=True)
                        deleted_age  += 1
                        deleted_size += size
                        logger.info(
                            f"Storage [36h rule]: deleted {f.name} "
                            f"(age {age_seconds/3600:.1f}h, {size/1024/1024:.1f} MB)"
                        )

                if deleted_age > 0:
                    logger.info(
                        f"Storage [36h rule]: removed {deleted_age} file(s), "
                        f"{deleted_size/1024/1024:.1f} MB freed"
                    )

                # ── Rule 2: Keep disk under 50% ────────────────────────────
                disk = shutil.disk_usage(str(rec_dir))
                used_pct = disk.used / disk.total * 100

                if used_pct > MAX_DISK_PERCENT:
                    logger.warning(
                        f"Storage [50% rule]: disk at {used_pct:.1f}% — cleaning up"
                    )
                    # Re-scan after age deletions
                    remaining = sorted(
                        [f for f in rec_dir.rglob("*.mp4") if f.is_file()],
                        key=lambda f: f.stat().st_mtime
                    )
                    deleted_disk = 0
                    for f in remaining:
                        disk = shutil.disk_usage(str(rec_dir))
                        used_pct = disk.used / disk.total * 100
                        if used_pct <= MAX_DISK_PERCENT:
                            break
                        size = f.stat().st_size
                        f.unlink(missing_ok=True)
                        deleted_disk += 1
                        logger.info(
                            f"Storage [50% rule]: deleted {f.name} "
                            f"({size/1024/1024:.1f} MB, disk now "
                            f"{(disk.used-size)/disk.total*100:.1f}%)"
                        )

                    if deleted_disk > 0:
                        logger.info(
                            f"Storage [50% rule]: removed {deleted_disk} file(s)"
                        )
                else:
                    logger.info(
                        f"Storage OK: disk at {used_pct:.1f}% "
                        f"(limit 50%, age limit 36h)"
                    )

            except Exception as e:
                logger.error(f"Storage watchdog error: {e}")'''


def patch():
    with open(RECORDER_PATH, 'r') as f:
        content = f.read()

    if OLD_WATCHDOG in content:
        content = content.replace(OLD_WATCHDOG, NEW_WATCHDOG, 1)
        with open(RECORDER_PATH, 'w') as f:
            f.write(content)

        # Syntax check
        import ast
        ast.parse(content)
        print("OK: Storage watchdog patched successfully")
        print("OK: Syntax check passed")
        print("")
        print("Now run:")
        print("  systemctl kill -s SIGKILL oeil-api && sleep 3 && systemctl start oeil-api")
        print("  journalctl -u oeil-api -f | grep -i storage")
    else:
        print("ERROR: Pattern not found in recorder.py")
        print("The watchdog may have already been patched or the file changed.")
        print("Check manually: grep -n '_storage_watchdog' /opt/oeil/services/recorder.py")

if __name__ == "__main__":
    patch()
