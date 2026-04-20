#!/bin/bash
# Oeil — go2rtc secure launcher
# Decrypts camera credentials in memory — plaintext never written to disk

# Use Python to read and decrypt directly from oeil.env
export CAM_USER=$(python3 -c "
import sys; sys.path.insert(0, '/opt/oeil')
from services.crypto_service import get_decrypted_env
print(get_decrypted_env('CAM_USER', 'admin'))
")

export CAM_PASS=$(python3 -c "
import sys; sys.path.insert(0, '/opt/oeil')
from services.crypto_service import get_decrypted_env
print(get_decrypted_env('CAM_PASS', ''))
")

echo "go2rtc launching with CAM_USER=$CAM_USER CAM_PASS=${#CAM_PASS} chars"
exec /usr/local/bin/go2rtc -config /etc/oeil/go2rtc.yaml
