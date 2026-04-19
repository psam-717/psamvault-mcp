import json
import os
from pathlib import Path

# the user must already be logged in via "psamvault login" before the MCP
# server can access their vault

SESSION_FILE = Path.home() / ".psamvault" / "session.json"
CONFIG_FILE = Path.home() / ".psamvault" / "config.env"

def load_config() -> None:
    """
    Load PSAMVAULT_API_URL and PSAMVAULT_PEPPER from the CLI config file
    into os.environ so api_client.py and crypto.py pick them up correctly
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



def load_session() -> dict:
    """
    Load the psamvault session created by  psamvault login.
 
    The session file contains:
      access_token   — short-lived JWT for API authentication
      refresh_token  — long-lived token for renewing the access token
      vek            — the raw Vault Encryption Key (hex-encoded 32 bytes)
                       decrypted locally by the CLI at login time using:
                       login_password → HMAC(pepper) → master_password
                       → PBKDF2(kdf_salt) → login_key
                       → AES-GCM-decrypt(encrypted_vek) → VEK
                       The VEK is the direct key for all vault entries.
 
    The server never has the VEK — it only stores the AES-GCM-encrypted
    version (encrypted_vek). Only the client can produce the raw VEK.
 
    Raises:
        RuntimeError: If the session file does not exist.
    """
    if not SESSION_FILE.exists():
        raise RuntimeError(
            "psamvault session not found. "
            "Run psamvault login in your terminal first"
        )
    return json.loads(SESSION_FILE.read_text())


def is_logged_in() -> bool:
    """Return True if the user is logged in"""
    return SESSION_FILE.exists()


def get_access_token() -> str:
    """Return the current access token from the session"""
    return load_session()["access_token"]


def get_vek() -> bytes:
    """Return the Vault Encryption Key (VEK) as raw bytes.
 
    The VEK is stored as a hex string in the session file after being
    decrypted locally at login. It is the direct AES-256 key used to
    encrypt and decrypt every vault entry — no further derivation needed.
 
    Returns:
        32 raw bytes — the AES-256 vault encryption key.
    """
    session = load_session()
    vek_hex = session.get("vek")
    if not vek_hex:
        raise RuntimeError(
            "VEK not found in session. "
            "This session was created with an older version of psamvault. "
            "Run  psamvault login  again to refresh your session."
        )
    return bytes.fromhex(vek_hex)


def get_refresh_token() -> str:
    """Return the current refresh token from the session"""
    return load_session()["refresh_token"]


def update_tokens(access_token: str, refresh_token: str) -> None:
    """
    Overwrite both access_token and refresh_token in the session file.
    Called after a successful token rotation so the new refresh token
    is persisted — without this the old revoked token gets reused on
    the next request, causing a permanent 401 loop.
    """
    session = load_session()
    session["access_token"] = access_token
    session["refresh_token"] = refresh_token
    SESSION_FILE.write_text(json.dumps(session, indent=2))


def get_kdf_salt() -> str:
    """Return the kdf salt from the session"""
    return load_session()["kdf_salt"]
        