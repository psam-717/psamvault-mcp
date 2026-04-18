import json

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def decrypt_credentials(
    vek: bytes,
    encrypted_blob: str,
    iv: str,
) -> dict:
    """
    Decrypt a vault entry blob using AES-256-GCM with the VEK.
 
    The VEK (Vault Encryption Key) is a random 32-byte key generated
    at signup and stored in the session file after login. It is the
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
    
    