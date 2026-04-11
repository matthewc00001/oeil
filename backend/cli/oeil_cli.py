# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

#!/usr/bin/env python3
"""
oeil-cli — Command line management tool for Oeil
Usage: oeil-cli <command> [options]

Commands:
  status                   Show system and camera status
  cameras list             List all cameras
  cameras add              Add a camera interactively
  cameras import           Import cameras from cameras.yaml
  cameras export           Export cameras to cameras.yaml
  cameras arm <id|all>     Arm camera(s)
  cameras disarm <id|all>  Disarm camera(s)
  recordings list          List recent recordings
  recordings clean         Delete recordings older than N days
  anpr list                List recent plate detections
  anpr watchlist           Show watchlist
  anpr watchlist-add       Add plate to watchlist
  anpr watchlist-remove    Remove plate from watchlist
  alerts list              List unread alerts
  alerts clear             Mark all alerts as read
  schedules show           Show arming schedules
  config show              Show current configuration
  discover                 Run ONVIF network discovery
  logs                     Tail service logs
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

# Allow running from install dir
sys.path.insert(0, "/opt/oeil")

# ── Colour helpers ─────────────────────────────────────────────────────────────
R = "\033[0;31m"; G = "\033[0;32m"; Y = "\033[1;33m"
C = "\033[0;36m"; B = "\033[1m";    N = "\033[0m"

def ok(msg):   print(f"{G}✓{N}  {msg}")
def err(msg):  print(f"{R}✗{N}  {msg}"); sys.exit(1)
def info(msg): print(f"{C}→{N}  {msg}")
def head(msg): print(f"\n{B}{C}{msg}{N}\n")
def row(label, value, color=""): print(f"  {label:<28} {color}{value}{N}")


# ── DB access (direct, no HTTP needed) ────────────────────────────────────────
async def get_cameras():
    from database import Camera, AsyncSessionLocal
    from sqlmodel import select
    async with AsyncSessionLocal() as session:
        result = await session.exec(select(Camera))
        return result.all()

async def get_recordings(limit=20):
    from database import Recording, AsyncSessionLocal
    from sqlmodel import select
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(Recording).order_by(Recording.started_at.desc()).limit(limit)
        )
        return result.all()

async def get_alerts(unread=True):
    from database import Alert, AsyncSessionLocal
    from sqlmodel import select
    async with AsyncSessionLocal() as session:
        q = select(Alert).order_by(Alert.created_at.desc()).limit(50)
        if unread:
            q = q.where(Alert.read == False)
        result = await session.exec(q)
        return result.all()


# ── Commands ───────────────────────────────────────────────────────────────────

async def cmd_status():
    head("Oeil System Status")
    # Service status
    services = ["oeil-api", "oeil-go2rtc", "nginx"]
    for svc in services:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True, text=True
            )
            active = result.stdout.strip() == "active"
            state = f"{G}● active{N}" if active else f"{R}● inactive{N}"
            row(svc, "", "")
            print(f"  {svc:<28} {state}")
        except Exception:
            row(svc, "unknown", Y)

    print()
    cameras = await get_cameras()
    online = sum(1 for c in cameras if c.status == "online")
    row("Total cameras",    str(len(cameras)))
    row("Online",           str(online), G)
    row("Offline",          str(len(cameras) - online), R if (len(cameras) - online) else N)

    import shutil
    from config import settings
    try:
        disk = shutil.disk_usage(str(settings.OW_DATA_DIR))
        used_gb = disk.used / 1024**3
        total_gb = disk.total / 1024**3
        pct = disk.used / disk.total * 100
        color = R if pct > 85 else Y if pct > 70 else G
        row("Storage used", f"{used_gb:.1f} GB / {total_gb:.1f} GB ({pct:.0f}%)", color)
    except Exception:
        pass


async def cmd_cameras_list():
    head("Cameras")
    cameras = await get_cameras()
    if not cameras:
        print("  No cameras configured. Run: oeil-cli cameras import")
        return
    print(f"  {'NAME':<20} {'HOST':<16} {'STATUS':<10} {'ARMED':<8} {'MODEL'}")
    print(f"  {'-'*70}")
    for c in cameras:
        status_color = G if c.status == "online" else R
        armed = f"{G}armed{N}" if c.armed else f"{Y}disarmed{N}"
        model = c.model or c.manufacturer or "—"
        print(f"  {c.name:<20} {c.host:<16} "
              f"{status_color}{c.status:<10}{N} {armed:<8}  {model}")
    print(f"\n  Total: {len(cameras)} camera(s)")


async def cmd_cameras_import():
    head("Import Cameras from YAML")
    from services.camera_import import import_cameras_from_yaml
    from database import init_db
    await init_db()
    result = await import_cameras_from_yaml()
    ok(f"Created: {result['created']}  |  Skipped: {result['skipped']}  |  Errors: {result['errors']}")


async def cmd_cameras_export():
    head("Export Cameras to YAML")
    from services.camera_import import export_cameras_to_yaml
    yaml_str = await export_cameras_to_yaml()
    print(yaml_str)


async def cmd_cameras_arm(target: str):
    from database import Camera, AsyncSessionLocal
    from sqlmodel import select
    async with AsyncSessionLocal() as session:
        if target == "all":
            result = await session.exec(select(Camera))
            cameras = result.all()
        else:
            result = await session.exec(select(Camera).where(
                (Camera.id == target) | (Camera.name == target)
            ))
            cameras = result.all()
        for c in cameras:
            c.armed = True
            ok(f"Armed: {c.name}")
        await session.commit()


async def cmd_cameras_disarm(target: str):
    from database import Camera, AsyncSessionLocal
    from sqlmodel import select
    async with AsyncSessionLocal() as session:
        if target == "all":
            result = await session.exec(select(Camera))
            cameras = result.all()
        else:
            result = await session.exec(select(Camera).where(
                (Camera.id == target) | (Camera.name == target)
            ))
            cameras = result.all()
        for c in cameras:
            c.armed = False
            ok(f"Disarmed: {c.name}")
        await session.commit()


async def cmd_recordings_list(limit: int = 20):
    head(f"Recent Recordings (last {limit})")
    recordings = await get_recordings(limit)
    if not recordings:
        print("  No recordings found.")
        return
    print(f"  {'DATE':<20} {'CAMERA':<20} {'DURATION':<10} {'SIZE':<10} {'TAGS'}")
    print(f"  {'-'*70}")
    for r in recordings:
        dur = f"{r.duration_seconds:.0f}s" if r.duration_seconds else "—"
        size = f"{r.size_bytes/1024/1024:.1f}MB" if r.size_bytes else "—"
        tags = []
        if r.has_person:    tags.append("person")
        if r.has_vehicle:   tags.append("vehicle")
        if r.has_intrusion: tags.append("intrusion")
        tag_str = ", ".join(tags) or "—"
        date_str = r.started_at.strftime("%Y-%m-%d %H:%M:%S")
        print(f"  {date_str:<20} {r.camera_id[:18]:<20} {dur:<10} {size:<10} {tag_str}")


async def cmd_recordings_clean(days: int):
    from database import Recording, AsyncSessionLocal
    from sqlmodel import select
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)
    deleted = 0
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(Recording).where(Recording.started_at < cutoff)
        )
        for r in result.all():
            Path(r.filepath).unlink(missing_ok=True)
            if r.thumbnail_path:
                Path(r.thumbnail_path).unlink(missing_ok=True)
            await session.delete(r)
            deleted += 1
        await session.commit()
    ok(f"Deleted {deleted} recording(s) older than {days} days")


async def cmd_anpr_list(limit: int = 20):
    head(f"Recent ANPR Detections (last {limit})")
    from services.anpr import ANPRService
    from services.event_bus import EventBus
    from services.notification import NotificationService
    from config import settings
    anpr = ANPRService(EventBus(), None, settings.OW_SNAPSHOTS_DIR)
    detections = await anpr.search_plates(limit=limit)
    if not detections:
        print("  No ANPR detections found.")
        return
    print(f"  {'DATE':<20} {'PLATE':<14} {'CAMERA':<20} {'CONF':<8} {'WATCHLIST'}")
    print(f"  {'-'*70}")
    for d in detections:
        wl = f"{R}HIT [{d.watchlist_tag}]{N}" if d.watchlist_match else "—"
        date_str = d.created_at.strftime("%Y-%m-%d %H:%M:%S")
        print(f"  {date_str:<20} {d.plate_number:<14} {d.camera_name:<20} {d.confidence:.0%}    {wl}")


async def cmd_anpr_watchlist():
    head("ANPR Watchlist")
    from services.anpr import ANPRService
    from services.event_bus import EventBus
    from config import settings
    anpr = ANPRService(EventBus(), None, settings.OW_SNAPSHOTS_DIR)
    await anpr._load_watchlist()
    entries = await anpr.get_watchlist()
    if not entries:
        print("  Watchlist is empty.")
        return
    print(f"  {'PLATE':<16} {'TAG':<12} {'NOTES'}")
    print(f"  {'-'*50}")
    for e in entries:
        print(f"  {e.plate_number:<16} {e.tag:<12} {e.notes or '—'}")


async def cmd_anpr_watchlist_add(plate: str, tag: str, notes: str):
    from services.anpr import ANPRService
    from services.event_bus import EventBus
    from config import settings
    anpr = ANPRService(EventBus(), None, settings.OW_SNAPSHOTS_DIR)
    entry = await anpr.add_to_watchlist(plate, tag, notes)
    ok(f"Added to watchlist: {entry.plate_number} [{entry.tag}]")


async def cmd_anpr_watchlist_remove(plate: str):
    from services.anpr import PlateWatchlist, ANPRService
    from services.event_bus import EventBus
    from config import settings
    from database import AsyncSessionLocal
    from sqlmodel import select
    norm = ANPRService._normalize(plate)
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(PlateWatchlist).where(PlateWatchlist.plate_normalized == norm)
        )
        entry = result.first()
        if not entry:
            err(f"Plate not found in watchlist: {plate}")
        entry.active = False
        await session.commit()
    ok(f"Removed from watchlist: {plate}")


async def cmd_alerts_list():
    head("Unread Alerts")
    alerts = await get_alerts(unread=True)
    if not alerts:
        ok("No unread alerts")
        return
    for a in alerts:
        color = R if a.severity == "critical" else Y if a.severity == "warning" else C
        date_str = a.created_at.strftime("%Y-%m-%d %H:%M")
        print(f"  {color}[{a.severity.upper()}]{N} {date_str}  {a.title}")
        print(f"         {a.body}")


async def cmd_alerts_clear():
    from database import Alert, AsyncSessionLocal
    from sqlmodel import select
    async with AsyncSessionLocal() as session:
        result = await session.exec(select(Alert).where(Alert.read == False))
        count = 0
        for a in result.all():
            a.read = True
            count += 1
        await session.commit()
    ok(f"Cleared {count} alert(s)")


async def cmd_schedules_show():
    head("Arming Schedules")
    from services.scheduler import ScheduleService
    svc = ScheduleService()
    rules = await svc.get_rules()
    if not rules:
        print("  No schedules configured.")
        print("  Edit via: PUT /api/schedules or web UI → Settings → Schedules")
        return
    days_map = {0:"Mon",1:"Tue",2:"Wed",3:"Thu",4:"Fri",5:"Sat",6:"Sun"}
    for r in rules:
        days = ", ".join(days_map[d] for d in r.get("days", []))
        print(f"  {B}{r['name']}{N}")
        print(f"    Days:    {days}")
        print(f"    Arm:     {r.get('arm_time','—')}  →  Disarm: {r.get('disarm_time','—')}")
        cams = r.get('camera_ids', ['all'])
        print(f"    Cameras: {', '.join(cams)}\n")


def cmd_config_show():
    head("Configuration (/etc/oeil/oeil.env)")
    env_file = Path("/etc/oeil/oeil.env")
    if not env_file.exists():
        err("Config file not found: /etc/oeil/oeil.env")
    for line in env_file.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        if "PASS" in line or "SECRET" in line:
            key = line.split("=")[0]
            print(f"  {key}=***")
        else:
            print(f"  {line}")


def cmd_discover():
    head("ONVIF Network Discovery")
    info("Scanning for ONVIF cameras on local network…")
    try:
        from wsdiscovery import WSDiscovery
        wsd = WSDiscovery()
        wsd.start()
        services = wsd.searchServices(types=["NetworkVideoTransmitter"])
        wsd.stop()
        if not services:
            print("  No ONVIF cameras found.")
        for svc in services:
            print(f"  Found: {svc.getXAddrs()}")
    except Exception as e:
        err(f"Discovery failed: {e}")


def cmd_logs(service: str = "oeil-api"):
    os.execvp("journalctl", ["journalctl", "-fu", service, "--no-pager"])


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(prog="oeil-cli", description="Oeil CLI")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("status")
    sub.add_parser("discover")
    sub.add_parser("config")

    cam_p = sub.add_parser("cameras")
    cam_s = cam_p.add_subparsers(dest="subcmd")
    cam_s.add_parser("list")
    cam_s.add_parser("import")
    cam_s.add_parser("export")
    arm_p = cam_s.add_parser("arm");    arm_p.add_argument("target", nargs="?", default="all")
    dis_p = cam_s.add_parser("disarm"); dis_p.add_argument("target", nargs="?", default="all")

    rec_p = sub.add_parser("recordings")
    rec_s = rec_p.add_subparsers(dest="subcmd")
    rl_p  = rec_s.add_parser("list");  rl_p.add_argument("--limit", type=int, default=20)
    rc_p  = rec_s.add_parser("clean"); rc_p.add_argument("--days", type=int, default=30)

    anpr_p = sub.add_parser("anpr")
    anpr_s = anpr_p.add_subparsers(dest="subcmd")
    al_p   = anpr_s.add_parser("list"); al_p.add_argument("--limit", type=int, default=20)
    anpr_s.add_parser("watchlist")
    wl_add = anpr_s.add_parser("watchlist-add")
    wl_add.add_argument("plate");  wl_add.add_argument("--tag", default="flagged")
    wl_add.add_argument("--notes", default="")
    wl_rm  = anpr_s.add_parser("watchlist-remove"); wl_rm.add_argument("plate")

    alert_p = sub.add_parser("alerts")
    alert_s = alert_p.add_subparsers(dest="subcmd")
    alert_s.add_parser("list"); alert_s.add_parser("clear")

    sched_p = sub.add_parser("schedules")
    sched_s = sched_p.add_subparsers(dest="subcmd")
    sched_s.add_parser("show")

    log_p = sub.add_parser("logs")
    log_p.add_argument("service", nargs="?", default="oeil-api")

    args = p.parse_args()

    if args.cmd == "status":
        asyncio.run(cmd_status())
    elif args.cmd == "discover":
        cmd_discover()
    elif args.cmd == "config":
        cmd_config_show()
    elif args.cmd == "logs":
        cmd_logs(args.service)
    elif args.cmd == "cameras":
        if args.subcmd == "list":    asyncio.run(cmd_cameras_list())
        elif args.subcmd == "import": asyncio.run(cmd_cameras_import())
        elif args.subcmd == "export": asyncio.run(cmd_cameras_export())
        elif args.subcmd == "arm":    asyncio.run(cmd_cameras_arm(args.target))
        elif args.subcmd == "disarm": asyncio.run(cmd_cameras_disarm(args.target))
        else: cam_p.print_help()
    elif args.cmd == "recordings":
        if args.subcmd == "list":    asyncio.run(cmd_recordings_list(args.limit))
        elif args.subcmd == "clean": asyncio.run(cmd_recordings_clean(args.days))
        else: rec_p.print_help()
    elif args.cmd == "anpr":
        if args.subcmd == "list":             asyncio.run(cmd_anpr_list(args.limit))
        elif args.subcmd == "watchlist":      asyncio.run(cmd_anpr_watchlist())
        elif args.subcmd == "watchlist-add":  asyncio.run(cmd_anpr_watchlist_add(args.plate, args.tag, args.notes))
        elif args.subcmd == "watchlist-remove": asyncio.run(cmd_anpr_watchlist_remove(args.plate))
        else: anpr_p.print_help()
    elif args.cmd == "alerts":
        if args.subcmd == "list":   asyncio.run(cmd_alerts_list())
        elif args.subcmd == "clear": asyncio.run(cmd_alerts_clear())
        else: alert_p.print_help()
    elif args.cmd == "schedules":
        if args.subcmd == "show": asyncio.run(cmd_schedules_show())
        else: sched_p.print_help()
    else:
        p.print_help()


if __name__ == "__main__":
    main()
