import json
import os
from pathlib import Path
from urllib.parse import urlparse

import keyring
import keyring.errors

# the user must already be logged in via "psamvault login" before the MCP
# server can access their vault

_SERVICE = "psamvault"
_PEPPER_KEY = "config.pepper"

_SESSION_KEYS = [
    "session.access_token",
    "session.refresh_token",
    "session.kdf_salt",
    "session.vek",
    "session.encrypted_vek",
    "session.vek_iv",
]

SESSION_FILE = Path.home() / ".psamvault" / "session.json"
CONFIG_FILE = Path.home() / ".psamvault" / "config.env"

# Run legacy migration on import so plaintext session.json is moved
# into the OS keychain before any credential is accessed.

def load_config() -> None:
    """
    Load configuration into os.environ so api_client.py and crypto.py
    pick them up via os.getenv().

    PSAMVAULT_API_URL is read from config.env (non-sensitive).
    PSAMVAULT_PEPPER is read from the OS keychain (sensitive).

    Migrates automatically from the old all-in-config.env format on first
    run after upgrade: if config.env contains PSAMVAULT_PEPPER, it is moved
    to the keychain and removed from the file.

    Only HTTPS URLs are accepted for PSAMVAULT_API_URL. A non-HTTPS or
    malformed value is silently ignored so a compromised config file
    cannot redirect credential-bearing requests to a plain-HTTP server.
    """
    if CONFIG_FILE.exists():
        lines = CONFIG_FILE.read_text().splitlines()
        filtered = list(lines)

        for i, line in enumerate(lines):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not key or not value:
                continue
            # Migration: pepper in config.env → move to keychain
            if key == "PSAMVAULT_PEPPER":
                if value and not keyring.get_password(_SERVICE, _PEPPER_KEY):
                    keyring.set_password(_SERVICE, _PEPPER_KEY, value)
                filtered[i] = None  # Mark for removal
                continue
            # Validate PSAMVAULT_API_URL: must be HTTPS to prevent redirect of
            # credential-bearing proxy calls to an attacker-controlled server.
            if key == "PSAMVAULT_API_URL":
                _parsed = urlparse(value)
                if _parsed.scheme != "https" or not _parsed.netloc:
                    filtered[i] = None  # Reject non-HTTPS or malformed API base URLs
                    continue
            if key not in os.environ:
                os.environ[key] = value

        # Rewrite config.env without pepper line (migration cleanup)
        cleaned = [l for l in filtered if l is not None]
        if len(cleaned) != len(lines):
            CONFIG_FILE.write_text("\n".join(cleaned) + "\n")

    # Load pepper from keychain into os.environ
    pepper = keyring.get_password(_SERVICE, _PEPPER_KEY)
    if pepper and "PSAMVAULT_PEPPER" not in os.environ:
        os.environ["PSAMVAULT_PEPPER"] = pepper


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
    if not SESSION_FILE.exists():
        return
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
        "encrypted_vek": "session.encrypted_vek",
        "vek_iv":        "session.vek_iv",
    }
    for field, keychain_key in field_map.items():
        if field in old_data:
            keyring.set_password(_SERVICE, keychain_key, old_data[field])

    # Replace file with empty presence marker
    SESSION_FILE.write_text("{}")


def is_logged_in() -> bool:
    """
    Return True if a psamvault session is active.

    Checks for both the presence marker file AND that the access token
    exists in the OS keychain. A mere file creation by another process
    cannot fake an active session — the keychain entry must also exist.
    """
    if not SESSION_FILE.exists():
        return False
    try:
        return keyring.get_password(_SERVICE, "session.access_token") is not None
    except Exception:
        return False


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


def update_access_token(access_token: str) -> None:
    """
    Overwrite just the access_token in the keychain.
    Called automatically after a successful token refresh.
    """
    keyring.set_password(_SERVICE, "session.access_token", access_token)


def clear_session() -> None:
    """
    Delete all session data from the keychain and remove the presence marker.
    """
    for key in _SESSION_KEYS:
        try:
            keyring.delete_password(_SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            pass
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()


_migrate_legacy_session()
