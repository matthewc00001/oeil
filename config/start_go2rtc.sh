#!/bin/bash
# Oeil — go2rtc secure launcher
# Decrypts camera credentials and injects as env vars
# Plaintext password never written to disk

source /etc/oeil/oeil.env

export CAM_USER="$CAM_USER"
export CAM_PASS=$(/opt/oeil/venv/bin/python3 -c "
import sys; sys.path.insert(0, '/opt/oeil')
from services.crypto_service import decrypt_value
import os
print(decrypt_value(os.environ.get('CAM_PASS', '')))
")

exec /usr/local/bin/go2rtc -config /etc/oeil/go2rtc.yaml
