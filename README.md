# 👁 Oeil

> **Oeil** (French for *Eye*) — Open-source AI edge camera surveillance backend  
> by **Mathieu Cadi** — [Openema SARL](https://openema.fr)  
> Released: April 11, 2026 · License: MIT

Oeil is a self-hosted video surveillance platform built for **Ability Enterprise AI edge cameras**.  
Runs entirely inside a **Debian 12 VM on Proxmox** — no cloud, no subscriptions, no limits.

```
Ability AI Camera  ──RTSP──►  go2rtc  ──HLS/WebRTC──►  Browser
        │                        │
        └──ONVIF/webhook──►  Oeil API (FastAPI)
                                 │
                           SQLite · FFmpeg · ANPR · Scheduler
                                 │
                              Nginx (port 80/443)
```

## Features

| Feature | Description |
|---|---|
| 🎥 **Live streams** | HLS + WebRTC via go2rtc, zero re-encode |
| 📹 **Event recording** | FFmpeg, triggered by AI events, pre/post-roll |
| 🧠 **ONVIF** | Auto-discovery, stream URI, PullPoint events |
| 🚘 **ANPR** | Plate storage, history, watchlist, instant alerts |
| 📸 **Snapshots** | On-demand + scheduled |
| 🕐 **Scheduler** | Arm/disarm by time-of-day and day-of-week |
| 🔔 **Alerts** | Email (SMTP), HTTP webhook, MQTT |
| 🖥 **Web UI** | Live view, recordings, ANPR console, settings |
| ⌨️ **CLI** | `oeil-cli` — full terminal management |
| 🔒 **Auth** | JWT, bcrypt |

## Quick Start

```bash
git clone https://github.com/openema/oeil.git
cd oeil
sudo bash install.sh
```

Then open `http://<vm-ip>` · Default login: `admin` / `changeme`

## Camera Setup

Edit `/etc/oeil/cameras.yaml`:

```yaml
cameras:
  - name: "Front Door"
    protocol: onvif
    host: 192.168.1.101
    username: admin
    password: "yourpassword"
```

```bash
oeil-cli cameras import
```

Set the camera's **HTTP event notification URL** to:
```
http://<oeil-vm-ip>/api/webhook/camera-event
```

## CLI

```bash
oeil-cli status
oeil-cli cameras list
oeil-cli cameras import
oeil-cli anpr watchlist-add AB-123-CD --tag blocked
oeil-cli recordings clean --days 30
oeil-cli logs
```

## API

Interactive docs: `http://<vm-ip>/api/docs`

## Supported Cameras

| Model | Features |
|---|---|
| Ability VS15410 AI-Eye | Person, vehicle, intrusion, trip-wire |
| Ability VS12100 AI ANPR | LPR, Super HDR PRO |
| Ability VS12112 AI ANPR | Motorized bullet, LPR |
| Ability AI-Vue / AI-Cube | Intel Movidius VPU, OpenVINO |
| Any ONVIF camera | Motion detection, RTSP stream |

## Author & License

**Mathieu Cadi** — [Openema SARL](https://openema.fr)  
Released April 11, 2026 under the [MIT License](LICENSE).
