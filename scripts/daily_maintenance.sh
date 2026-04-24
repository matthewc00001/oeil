#!/bin/bash
# =============================================================================
# Oeil VMS — Daily Maintenance Script
# Author: Mathieu Cadi — Openema SARL
# Version: 1.0 — April 24, 2026
#
# Purpose:
#   - Delete recordings older than yesterday (files + DB in sync)
#   - Clean orphaned files not in DB
#   - Clean orphaned DB entries with no file
#   - Report disk usage
#
# Schedule: Weekdays after 9:30 AM (learning window ends)
# Cron:     30 9 * * 1-5 /opt/oeil/scripts/daily_maintenance.sh >> /var/log/oeil/maintenance.log 2>&1
# =============================================================================

set -euo pipefail

DB="/var/lib/oeil/db/oeil.db"
RECORDINGS="/var/lib/oeil/recordings"
LOG_PREFIX="[OEIL-MAINTENANCE $(date '+%Y-%m-%d %H:%M:%S')]"

echo ""
echo "============================================================"
echo "$LOG_PREFIX START"
echo "============================================================"

# ── Safety check: only run on weekdays after 9:30 AM ─────────────────────────
HOUR=$(date +%H)
MINUTE=$(date +%M)
DOW=$(date +%u)  # 1=Mon, 5=Fri, 6=Sat, 7=Sun
TIME_MINS=$((10#$HOUR * 60 + 10#$MINUTE))

if [ "$DOW" -ge 6 ]; then
    echo "$LOG_PREFIX SKIP — weekend, maintenance runs weekdays only"
    exit 0
fi

if [ "$TIME_MINS" -lt 570 ]; then  # 570 = 9h30
    echo "$LOG_PREFIX SKIP — too early (before 09:30), learning window may still be active"
    exit 0
fi

# ── Disk before ──────────────────────────────────────────────────────────────
DISK_BEFORE=$(df -h / | awk 'NR==2 {print $5}')
echo "$LOG_PREFIX Disk usage before: $DISK_BEFORE"

# ── Step 1: Delete recordings older than today from filesystem + DB ───────────
CUTOFF=$(date '+%Y-%m-%d 00:00:00')
echo "$LOG_PREFIX Deleting recordings older than: $CUTOFF"

COUNT_BEFORE=$(sqlite3 "$DB" "SELECT COUNT(*) FROM recording WHERE started_at < '$CUTOFF';")
echo "$LOG_PREFIX Recordings to delete from DB: $COUNT_BEFORE"

if [ "$COUNT_BEFORE" -gt 0 ]; then
    # Delete files first
    sqlite3 "$DB" "SELECT camera_id, filename FROM recording WHERE started_at < '$CUTOFF';" \
    | while IFS='|' read -r cam file; do
        FILEPATH="$RECORDINGS/$cam/$file"
        if [ -f "$FILEPATH" ]; then
            rm -f "$FILEPATH"
        fi
    done

    # Delete DB entries
    sqlite3 "$DB" "DELETE FROM recording WHERE started_at < '$CUTOFF';"
    echo "$LOG_PREFIX Deleted $COUNT_BEFORE old recording(s) from DB and filesystem"
else
    echo "$LOG_PREFIX No old recordings to delete"
fi

# ── Step 2: Clean orphaned files (file exists but not in DB) ─────────────────
echo "$LOG_PREFIX Checking for orphaned files..."
ORPHAN_COUNT=0

find "$RECORDINGS" -name "*.mp4" | while read -r filepath; do
    filename=$(basename "$filepath")
    camera_id=$(basename "$(dirname "$filepath")")
    exists=$(sqlite3 "$DB" "SELECT COUNT(*) FROM recording WHERE camera_id='$camera_id' AND filename='$filename';")
    if [ "$exists" -eq 0 ]; then
        echo "$LOG_PREFIX Orphaned file removed: $filepath"
        rm -f "$filepath"
        ORPHAN_COUNT=$((ORPHAN_COUNT + 1))
    fi
done

echo "$LOG_PREFIX Orphaned file check complete"

# ── Step 3: Clean orphaned DB entries (in DB but file missing) ───────────────
echo "$LOG_PREFIX Checking for orphaned DB entries..."
ORPHAN_DB=0

sqlite3 "$DB" "SELECT id, camera_id, filename FROM recording;" \
| while IFS='|' read -r id cam file; do
    FILEPATH="$RECORDINGS/$cam/$file"
    if [ ! -f "$FILEPATH" ]; then
        sqlite3 "$DB" "DELETE FROM recording WHERE id='$id';"
        echo "$LOG_PREFIX Orphaned DB entry removed: $cam/$file"
        ORPHAN_DB=$((ORPHAN_DB + 1))
    fi
done

echo "$LOG_PREFIX Orphaned DB entry check complete"

# ── Step 4: Final report ──────────────────────────────────────────────────────
DISK_AFTER=$(df -h / | awk 'NR==2 {print $5}')
COUNT_AFTER=$(sqlite3 "$DB" "SELECT COUNT(*) FROM recording;")
DISK_PCT=$(df / | awk 'NR==2 {print $5}' | tr -d '%')

echo "$LOG_PREFIX Disk usage after:  $DISK_AFTER"
echo "$LOG_PREFIX Recordings in DB:  $COUNT_AFTER"

# ── Step 5: Warn if disk still above 45% after cleanup ───────────────────────
if [ "$DISK_PCT" -gt 45 ]; then
    echo "$LOG_PREFIX WARNING — Disk still at ${DISK_PCT}% after cleanup. Manual intervention may be needed."
fi

echo "$LOG_PREFIX DONE"
echo "============================================================"
