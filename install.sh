#!/usr/bin/env bash
# =============================================================================
# Oeil — AI Edge Camera Surveillance Backend
# Installer for Debian 12 (Bookworm) VM on Proxmox
#
# Author  : Mathieu Cadi
# Company : Openema SARL
# License : MIT
# Date    : April 11, 2026
# GitHub  : https://github.com/openema/oeil
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'; BOLD='\033[1m'

OW_USER="oeil"
OW_DIR="/opt/oeil"
OW_DATA="/var/lib/oeil"
OW_LOGS="/var/log/oeil"
OW_CONFIG="/etc/oeil"

log()     { echo -e "${GREEN}[✓]${NC} $*"; }
info()    { echo -e "${CYAN}[→]${NC} $*"; }
warn()    { echo -e "${YELLOW}[!]${NC} $*"; }
die()     { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }
section() { echo -e "\n${BOLD}${CYAN}══ $* ══${NC}\n"; }

[[ $EUID -eq 0 ]] || die "Run as root: sudo bash install.sh"

. /etc/os-release
[[ "$ID" == "debian" && "$VERSION_ID" == "12" ]] || \
  warn "Tested on Debian 12. Detected: $PRETTY_NAME — continuing anyway."

section "👁 Oeil Installer — Ability Enterprise AI Edge Cameras"
echo -e "  Author   : Mathieu Cadi — Openema SARL"
echo -e "  License  : MIT"
echo -e "  Install  : ${OW_DIR}"
echo -e "  Data     : ${OW_DATA}"
echo -e "  Config   : ${OW_CONFIG}"
echo ""
read -rp "  Continue? [y/N] " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

# ── 1 — Packages ──────────────────────────────────────────────────────────────
section "1 — System packages"
apt-get update -qq
apt-get install -y --no-install-recommends \
  python3 python3-pip python3-venv python3-dev \
  ffmpeg sqlite3 nginx curl wget git \
  build-essential libssl-dev libffi-dev \
  v4l-utils net-tools ufw certbot python3-certbot-nginx jq 2>/dev/null
log "System packages installed"

# ── 2 — go2rtc ────────────────────────────────────────────────────────────────
section "2 — go2rtc (RTSP/ONVIF proxy)"
GO2RTC_VER="1.9.4"
GO2RTC_BIN="/usr/local/bin/go2rtc"
if [[ ! -f "$GO2RTC_BIN" ]]; then
  ARCH=$(dpkg --print-architecture)
  case "$ARCH" in
    amd64) GO2RTC_URL="https://github.com/AlexxIT/go2rtc/releases/download/v${GO2RTC_VER}/go2rtc_linux_amd64" ;;
    arm64) GO2RTC_URL="https://github.com/AlexxIT/go2rtc/releases/download/v${GO2RTC_VER}/go2rtc_linux_arm64" ;;
    *)     die "Unsupported architecture: $ARCH" ;;
  esac
  info "Downloading go2rtc v${GO2RTC_VER}…"
  wget -q -O "$GO2RTC_BIN" "$GO2RTC_URL"
  chmod +x "$GO2RTC_BIN"
  log "go2rtc installed"
else
  log "go2rtc already present"
fi

# ── 3 — Python venv ───────────────────────────────────────────────────────────
section "3 — Python virtual environment"
useradd -r -s /sbin/nologin -d "$OW_DIR" "$OW_USER" 2>/dev/null || true
mkdir -p "$OW_DIR" "$OW_DATA"/{recordings,snapshots,db} "$OW_LOGS" "$OW_CONFIG"
python3 -m venv "$OW_DIR/venv"
"$OW_DIR/venv/bin/pip" install --upgrade pip -q
info "Installing Python dependencies…"
"$OW_DIR/venv/bin/pip" install -q -r "$(dirname "$0")/backend/requirements.txt"
log "Python environment ready"

# ── 4 — Application files ─────────────────────────────────────────────────────
section "4 — Application files"
cp -r "$(dirname "$0")"/backend/. "$OW_DIR"/
cp -r "$(dirname "$0")"/frontend/. "$OW_DIR"/frontend/
log "Application files copied"

# ── 5 — Configuration ─────────────────────────────────────────────────────────
section "5 — Configuration"
if [[ ! -f "$OW_CONFIG/oeil.env" ]]; then
  SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  cat > "$OW_CONFIG/oeil.env" << ENVEOF
# Oeil Configuration — Mathieu Cadi / Openema SARL
# Edit this file then: systemctl restart oeil-api

OW_HOST=0.0.0.0
OW_PORT=8090
OW_SECRET_KEY=${SECRET}
OW_DEBUG=false

OW_DATA_DIR=${OW_DATA}
OW_RECORDINGS_DIR=${OW_DATA}/recordings
OW_SNAPSHOTS_DIR=${OW_DATA}/snapshots
OW_DB_PATH=${OW_DATA}/db/oeil.db

OW_RECORD_ON_MOTION=true
OW_PRE_MOTION_SECONDS=5
OW_POST_MOTION_SECONDS=15
OW_MAX_STORAGE_GB=500
OW_SEGMENT_DURATION=300

OW_GO2RTC_API=http://127.0.0.1:1984
OW_GO2RTC_CONFIG=${OW_CONFIG}/go2rtc.yaml
OW_CAMERAS_CONFIG=${OW_CONFIG}/cameras.yaml

OW_SMTP_HOST=
OW_SMTP_PORT=587
OW_SMTP_USER=
OW_SMTP_PASS=
OW_ALERT_EMAIL=
OW_WEBHOOK_URL=
OW_MQTT_URL=

OW_ADMIN_USER=admin
OW_ADMIN_PASS=changeme
OW_TOKEN_EXPIRE_MINUTES=480
ENVEOF
  log "Config written to $OW_CONFIG/oeil.env"
  warn "IMPORTANT: Edit $OW_CONFIG/oeil.env — change OW_ADMIN_PASS"
else
  log "Config already exists — skipping"
fi

[[ -f "$OW_CONFIG/go2rtc.yaml" ]] || cp "$(dirname "$0")/config/go2rtc.yaml" "$OW_CONFIG/go2rtc.yaml"
[[ -f "$OW_CONFIG/cameras.yaml" ]] || cp "$(dirname "$0")/config/cameras.yaml" "$OW_CONFIG/cameras.yaml"
log "go2rtc and cameras config in place"

# ── 6 — Systemd ───────────────────────────────────────────────────────────────
section "6 — Systemd services"
cp "$(dirname "$0")"/systemd/oeil-*.service /etc/systemd/system/
systemctl daemon-reload
for svc in oeil-go2rtc oeil-api oeil-recorder; do
  systemctl enable "$svc"
  log "Enabled $svc"
done

# ── 7 — Nginx ─────────────────────────────────────────────────────────────────
section "7 — Nginx"
cp "$(dirname "$0")/config/nginx-oeil.conf" /etc/nginx/sites-available/oeil
ln -sf /etc/nginx/sites-available/oeil /etc/nginx/sites-enabled/oeil
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl enable nginx && systemctl restart nginx
log "Nginx configured"

# ── 8 — Firewall ──────────────────────────────────────────────────────────────
section "8 — Firewall"
ufw allow 22/tcp   comment "SSH"   2>/dev/null || true
ufw allow 80/tcp   comment "HTTP"  2>/dev/null || true
ufw allow 443/tcp  comment "HTTPS" 2>/dev/null || true
ufw allow 8090/tcp comment "Oeil API"   2>/dev/null || true
ufw allow 1984/tcp comment "go2rtc API" 2>/dev/null || true
ufw --force enable 2>/dev/null || true
log "Firewall configured"

# ── 9 — Permissions ───────────────────────────────────────────────────────────
section "9 — Permissions"
chown -R "$OW_USER:$OW_USER" "$OW_DIR" "$OW_DATA" "$OW_LOGS" "$OW_CONFIG"
chmod 750 "$OW_CONFIG"
chmod 640 "$OW_CONFIG/oeil.env"
log "Permissions set"

# ── 10 — Database ─────────────────────────────────────────────────────────────
section "10 — Database"
sudo -u "$OW_USER" "$OW_DIR/venv/bin/python3" "$OW_DIR/db_init.py"
log "Database initialized"

# ── 11 — CLI ──────────────────────────────────────────────────────────────────
section "11 — CLI"
ln -sf "$OW_DIR/cli/oeil_cli.py" /usr/local/bin/oeil-cli
chmod +x /usr/local/bin/oeil-cli
log "CLI installed → oeil-cli"

# ── 12 — Start ────────────────────────────────────────────────────────────────
section "12 — Start services"
for svc in oeil-go2rtc oeil-api oeil-recorder; do
  systemctl start "$svc" && log "Started $svc" || warn "Failed: $svc — check: journalctl -u $svc"
done

# ── Done ──────────────────────────────────────────────────────────────────────
HOST_IP=$(hostname -I | awk '{print $1}')
section "✅ Installation Complete"
echo -e "  ${BOLD}Application :${NC}  Oeil by Openema SARL"
echo -e "  ${BOLD}Web UI      :${NC}  http://${HOST_IP}"
echo -e "  ${BOLD}API docs    :${NC}  http://${HOST_IP}/api/docs"
echo -e "  ${BOLD}go2rtc      :${NC}  http://${HOST_IP}:1984"
echo ""
echo -e "  ${BOLD}Login       :${NC}  admin / changeme"
echo -e "  ${YELLOW}→ Change password in:${NC} $OW_CONFIG/oeil.env (OW_ADMIN_PASS)"
echo ""
echo -e "  ${BOLD}Add cameras :${NC}  edit $OW_CONFIG/cameras.yaml"
echo -e "              then run: oeil-cli cameras import"
echo ""
echo -e "  ${BOLD}Logs        :${NC}  journalctl -fu oeil-api"
echo -e "  ${BOLD}CLI         :${NC}  oeil-cli status"
echo ""
