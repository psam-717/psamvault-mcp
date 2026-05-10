import json
import os
from pathlib import Path

import keyring
import keyring.errors

# the user must already be logged in via "psamvault login" before the MCP
# server can access their vault

_SERVICE = "psamvault"

_SESSION_KEYS = [
    "session.access_token",
    "session.refresh_token",
    "session.kdf_salt",
    "session.vek",
]

SESSION_FILE = Path.home() / ".psamvault" / "session.json"
CONFIG_FILE = Path.home() / ".psamvault" / "config.env"

def load_config() -> None:
    """
    Load PSAMVAULT_API_URL from the CLI config file into os.environ so
    api_client.py picks it up correctly. config.env holds only the
    non-sensitive API URL — all secrets live in the OS keychain.
    """
    if not CONFIG_FILE.exists():
        return
    
    for line in CONFIG_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and value and key not in os.environ:
            os.environ[key] = value


def _get_keychain(key: str) -> str:
    """
    Read a value from the OS keychain under the psamvault service.

    Raises:
        RuntimeError: If the key is missing — the user needs to log in again.
    """
    value = keyring.get_password(_SERVICE, key)
    if value is None:
        raise RuntimeError(
            f"Session value '{key}' not found in keychain. "
            "Run  psamvault login  in your terminal to restore your session."
        )
    return value


def _migrate_legacy_session() -> None:
    """
    One-time migration: if session.json still holds the old plaintext fields
    from a pre-keychain version of psamvault, move them to the keychain and
    replace the file with an empty presence marker.
    """
    raw = SESSION_FILE.read_text().strip()
    if not raw or raw == "{}":
        return
    try:
        old_data = json.loads(raw)
    except json.JSONDecodeError:
        return
    if not old_data:
        return

    # Write any recognised fields into the keychain
    field_map = {
        "access_token":  "session.access_token",
        "refresh_token": "session.refresh_token",
        "kdf_salt":      "session.kdf_salt",
        "vek":           "session.vek",
    }
    for field, keychain_key in field_map.items():
        if field in old_data:
            keyring.set_password(_SERVICE, keychain_key, old_data[field])

    # Replace file with empty presence marker
    SESSION_FILE.write_text("{}")


def is_logged_in() -> bool:
    """
    Return True if a psamvault session is active.

    Checks only for the presence marker file — no keychain call needed.
    The CLI writes this file on login and removes it on logout.
    """
    return SESSION_FILE.exists()


def get_access_token() -> str:
    """Return the current access token from the keychain."""
    return _get_keychain("session.access_token")


def get_vek() -> bytes:
    """
    Return the Vault Encryption Key (VEK) as raw bytes.

    The VEK is the direct AES-256 key used to decrypt every vault entry.
    It is stored in the OS keychain after being decrypted locally at
    login time (login_password → HMAC → PBKDF2 → AES-GCM-decrypt → VEK).

    Returns:
        32 raw bytes — the AES-256 vault encryption key.
    """
    vek_hex = _get_keychain("session.vek")
    return bytes.fromhex(vek_hex)


def get_refresh_token() -> str:
    """Return the current refresh token from the keychain."""
    return _get_keychain("session.refresh_token")


def update_tokens(access_token: str, refresh_token: str) -> None:
    """
    Overwrite both access_token and refresh_token in the keychain.
    Called after a successful token rotation so the new refresh token
    is persisted — without this the old revoked token gets reused on
    the next request, causing a permanent 401 loop.
    """
    keyring.set_password(_SERVICE, "session.access_token", access_token)
    keyring.set_password(_SERVICE, "session.refresh_token", refresh_token)
