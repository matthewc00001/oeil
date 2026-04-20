# Copyright (c) 2026 Mathieu Cadi — Openema SARL
"""
Oeil — Cryptography Service
AES-256 (Fernet) encryption for all sensitive configuration values.
Secrets are stored encrypted in /etc/oeil/oeil.env.
The encryption key is derived from a master key stored separately.
Never store plaintext passwords in code or GitHub.
"""
from __future__ import annotations
import base64
import logging
import os
from pathlib import Path
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger("oeil.crypto")

KEY_FILE = Path("/etc/oeil/.oeil_master_key")
ENV_FILE = Path("/etc/oeil/oeil.env")

# Prefix to identify encrypted values in oeil.env
ENCRYPTED_PREFIX = "enc:"


def _get_or_create_master_key() -> bytes:
    """Load or generate the master encryption key."""
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes()
    else:
        # Generate a new Fernet key
        key = Fernet.generate_key()
        KEY_FILE.write_bytes(key)
        KEY_FILE.chmod(0o600)  # Owner read only
        logger.info("Generated new master encryption key")
        return key


def _get_fernet() -> Fernet:
    key = _get_or_create_master_key()
    return Fernet(key)


def encrypt_value(plaintext: str) -> str:
    """Encrypt a plaintext value. Returns enc:base64string"""
    if not plaintext:
        return plaintext
    if plaintext.startswith(ENCRYPTED_PREFIX):
        return plaintext  # Already encrypted
    f = _get_fernet()
    encrypted = f.encrypt(plaintext.encode())
    return ENCRYPTED_PREFIX + encrypted.decode()


def decrypt_value(value: str) -> str:
    """Decrypt an encrypted value. Returns plaintext."""
    if not value:
        return value
    if not value.startswith(ENCRYPTED_PREFIX):
        return value  # Not encrypted — return as-is
    try:
        f = _get_fernet()
        encrypted = value[len(ENCRYPTED_PREFIX):].encode()
        return f.decrypt(encrypted).decode()
    except Exception as e:
        logger.error(f"Failed to decrypt value: {e}")
        return ""


def encrypt_env_value(key: str, plaintext: str) -> bool:
    """
    Encrypt a value and update it in /etc/oeil/oeil.env.
    Returns True if successful.
    """
    try:
        encrypted = encrypt_value(plaintext)
        lines = ENV_FILE.read_text().splitlines()
        updated = False
        new_lines = []
        for line in lines:
            if line.startswith(f"{key}="):
                new_lines.append(f"{key}={encrypted}")
                updated = True
            else:
                new_lines.append(line)
        if not updated:
            new_lines.append(f"{key}={encrypted}")
        ENV_FILE.write_text("\n".join(new_lines) + "\n")
        logger.info(f"Encrypted and saved {key} to oeil.env")
        return True
    except Exception as e:
        logger.error(f"Failed to encrypt {key}: {e}")
        return False


def get_decrypted_env(key: str, default: str = "") -> str:
    """Read and decrypt a value from oeil.env."""
    try:
        lines = ENV_FILE.read_text().splitlines()
        for line in lines:
            if line.startswith(f"{key}="):
                value = line.split("=", 1)[1].strip()
                return decrypt_value(value)
        return default
    except Exception as e:
        logger.error(f"Failed to read {key}: {e}")
        return default
