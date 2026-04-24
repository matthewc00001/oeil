#!/bin/bash
# =============================================================================
# Oeil VMS — Weekend Maintenance Script
# Author: Mathieu Cadi — Openema SARL
# Version: 1.0 — April 24, 2026
#
# Purpose:
#   - Delete ALL recordings older than last run (clean slate each cycle)
#   - Clean orphaned files not in DB
#   - Clean orphaned DB entries with no file
#   - Report disk usage
#
# Active window: Friday 18:30 to Monday 07:45 (full alert weekend period)
# Schedule (cron):
#   0 6  * * 6,7   root /opt/oeil/scripts/weekend_maintenance.sh >> /var/log/oeil/maintenance.log 2>&1
#   0 18 * * 5,6,7 root /opt/oeil/scripts/weekend_maintenance.sh >> /var/log/oeil/maintenance.log 2>&1
#   0 6  * * 1     root /opt/oeil/scripts/weekend_maintenance.sh >> /var/log/oeil/maintenance.log 2>&1
#
# Retention: clean slate each run - switch to 36h once workers fully learned
# =============================================================================

set -euo pipefail

DB="/var/lib/oeil/db/oeil.db"
RECORDINGS="/var/lib/oeil/recordings"
LOG_PREFIX="[OEIL-WEEKEND-MAINTENANCE $(date '+%Y-%m-%d %H:%M:%S')]"

echo ""
echo "============================================================"
echo "$LOG_PREFIX START"
echo "============================================================"

# Safety check: only run during weekend alert window
# Friday 18:30 to Monday 07:45
HOUR=$(date +%H)
MINUTE=$(date +%M)
DOW=$(date +%u)
TIME_MINS=$((10#$HOUR * 60 + 10#$MINUTE))

IN_WINDOW=0

if [ "$DOW" -eq 6 ] || [ "$DOW" -eq 7 ]; then
    IN_WINDOW=1
fi

if [ "$DOW" -eq 5 ] && [ "$TIME_MINS" -ge 1110 ]; then
    IN_WINDOW=1
fi

if [ "$DOW" -eq 1 ] && [ "$TIME_MINS" -lt 465 ]; then
    IN_WINDOW=1
fi

if [ "$IN_WINDOW" -eq 0 ]; then
    echo "$LOG_PREFIX SKIP - outside weekend alert window (Fri 18:30 to Mon 07:45)"
    exit 0
fi

DISK_BEFORE=$(df -h / | awk 'NR==2 {print $5}')
echo "$LOG_PREFIX Disk usage before: $DISK_BEFORE"

CUTOFF=$(date '+%Y-%m-%d %H:%M:%S')
echo "$LOG_PREFIX Deleting all recordings before: $CUTOFF"

COUNT_BEFORE=$(sqlite3 "$DB" "SELECT COUNT(*) FROM recording WHERE started_at < '$CUTOFF';")
echo "$LOG_PREFIX Recordings to delete from DB: $COUNT_BEFORE"

if [ "$COUNT_BEFORE" -gt 0 ]; then
    sqlite3 "$DB" "SELECT camera_id, filename FROM recording WHERE started_at < '$CUTOFF';" \
    | while IFS='|' read -r cam file; do
        FILEPATH="$RECORDINGS/$cam/$file"
        if [ -f "$FILEPATH" ]; then
            rm -f "$FILEPATH"
        fi
    done
    sqlite3 "$DB" "DELETE FROM recording WHERE started_at < '$CUTOFF';"
    echo "$LOG_PREFIX Deleted $COUNT_BEFORE recording(s) from DB and filesystem"
else
    echo "$LOG_PREFIX No recordings to delete"
fi

echo "$LOG_PREFIX Checking for orphaned files..."
find "$RECORDINGS" -name "*.mp4" | while read -r filepath; do
    filename=$(basename "$filepath")
    camera_id=$(basename "$(dirname "$filepath")")
    exists=$(sqlite3 "$DB" "SELECT COUNT(*) FROM recording WHERE camera_id='$camera_id' AND filename='$filename';")
    if [ "$exists" -eq 0 ]; then
        echo "$LOG_PREFIX Orphaned file removed: $filepath"
        rm -f "$filepath"
    fi
done
echo "$LOG_PREFIX Orphaned file check complete"

echo "$LOG_PREFIX Checking for orphaned DB entries..."
sqlite3 "$DB" "SELECT id, camera_id, filename FROM recording;" \
| while IFS='|' read -r id cam file; do
    FILEPATH="$RECORDINGS/$cam/$file"
    if [ ! -f "$FILEPATH" ]; then
        sqlite3 "$DB" "DELETE FROM recording WHERE id='$id';"
        echo "$LOG_PREFIX Orphaned DB entry removed: $cam/$file"
    fi
done
echo "$LOG_PREFIX Orphaned DB entry check complete"

DISK_AFTER=$(df -h / | awk 'NR==2 {print $5}')
COUNT_AFTER=$(sqlite3 "$DB" "SELECT COUNT(*) FROM recording;")
DISK_PCT=$(df / | awk 'NR==2 {print $5}' | tr -d '%')

echo "$LOG_PREFIX Disk usage after:  $DISK_AFTER"
echo "$LOG_PREFIX Recordings in DB:  $COUNT_AFTER"

if [ "$DISK_PCT" -gt 45 ]; then
    echo "$LOG_PREFIX WARNING - Disk still at ${DISK_PCT}% after cleanup. Manual intervention may be needed."
fi

echo "$LOG_PREFIX DONE"
echo "============================================================"
