import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def decrypt_api_key(
    vek: bytes,
    encrypted_blob: str,
    iv: str,
) -> dict:
    """
    Decrypt an API key entry blob using AES-256-GCM with the VEK.

    The VEK (Vault Encryption Key) is a random 32-byte key generated
    at signup and stored in the OS keychain after login. It is the
    direct AES-256 key for all vault entries — no derivation needed here.

    Args:
        vek:            32-byte vault encryption key from get_vek().
        encrypted_blob: Hex-encoded AES-GCM ciphertext from the API.
        iv:             Hex-encoded 12-byte IV from the API.

    Returns:
        Dict with keys: service, api_key, notes.

    Raises:
        cryptography.exceptions.InvalidTag: If the VEK is wrong or the
        ciphertext has been tampered with.
    """

    aesgcm = AESGCM(vek)
    plaintext = aesgcm.decrypt(
        bytes.fromhex(iv),
        bytes.fromhex(encrypted_blob),
        None,
    )
    return json.loads(plaintext.decode("utf-8"))


def decrypt_credentials(
    vek: bytes,
    encrypted_blob: str,
    iv: str,
) -> dict:
    """
    Decrypt a vault entry blob using AES-256-GCM with the VEK.
 
    The VEK (Vault Encryption Key) is a random 32-byte key generated
    at signup and stored in the OS keychain after login. It is the
    direct AES-256 key for all vault entries — no derivation needed here.
 
    The full key hierarchy that produced this VEK was:
      login_password
        → HMAC-SHA256(pepper) → master_password
        → PBKDF2-HMAC-SHA256(kdf_salt, 600k iterations) → login_key
        → AES-256-GCM-decrypt(encrypted_vek, vek_iv) → VEK
    All of that happened in the CLI at login. The MCP server receives
    only the final VEK from the session file.
 
    Args:
        vek:            32-byte vault encryption key from get_vek().
        encrypted_blob: Hex-encoded AES-GCM ciphertext from the API.
        iv:             Hex-encoded 12-byte IV from the API.
 
    Returns:
        Dict with keys: username, password, notes.
 
    Raises:
        cryptography.exceptions.InvalidTag: If the VEK is wrong or the
        ciphertext has been tampered with.
    """
    
    aesgcm = AESGCM(vek)
    plaintext = aesgcm.decrypt(
        bytes.fromhex(iv),
        bytes.fromhex(encrypted_blob),
        None,
    )
    return json.loads(plaintext.decode("utf-8"))


def encrypt_api_key(
    vek: bytes,
    service: str,
    api_key: str,
    notes: str = "",
) -> tuple[str, str]:
    """
    Encrypt an API key bundle using AES-256-GCM.

    Bundles service name, the raw API key, and optional notes into a single
    JSON payload before encrypting — compatible with the CLI's format so
    `psamvault ak-get` can decrypt what we store.

    Args:
        vek:     32-byte VEK from the session.
        service: Human-readable service name, e.g. "Supabase".
        api_key: The plaintext API key string.
        notes:   Optional notes.

    Returns:
        (encrypted_blob_hex, iv_hex)
    """
    payload = json.dumps({
        "service": service,
        "api_key": api_key,
        "notes": notes,
    }).encode("utf-8")

    iv = os.urandom(12)
    aesgcm = AESGCM(vek)
    encrypted_blob = aesgcm.encrypt(iv, payload, None)

    return encrypted_blob.hex(), iv.hex()