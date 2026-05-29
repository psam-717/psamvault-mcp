"""Tests for crypto.py — AES-256-GCM credential decryption."""

import json
import os

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mcp_server.crypto import decrypt_credentials


class TestDecryptCredentials:
    """decrypt_credentials is the only public function in crypto.py.

    The CLI handles encryption; the MCP only needs to decrypt.
    """

    def test_decrypt_roundtrip(self, vek: bytes, sample_creds: dict):
        """A valid encrypted blob decrypts back to the original credentials."""
        encrypted_blob, iv = _encrypt(vek, sample_creds)
        result = decrypt_credentials(vek, encrypted_blob, iv)
        assert result == sample_creds

    def test_wrong_key_raises_invalid_tag(self, vek: bytes, sample_creds: dict):
        """Decrypting with a different key raises InvalidTag."""
        encrypted_blob, iv = _encrypt(vek, sample_creds)
        wrong_key = os.urandom(32)
        with pytest.raises(InvalidTag):
            decrypt_credentials(wrong_key, encrypted_blob, iv)

    def test_tampered_blob_raises_invalid_tag(self, vek: bytes, sample_creds: dict):
        """Modifying the ciphertext makes AES-GCM authentication fail."""
        encrypted_blob, iv = _encrypt(vek, sample_creds)
        blob_bytes = bytes.fromhex(encrypted_blob)
        tampered = bytearray(blob_bytes)
        tampered[0] ^= 0xFF  # flip one bit
        with pytest.raises(InvalidTag):
            decrypt_credentials(vek, tampered.hex(), iv)

    def test_tampered_iv_raises_invalid_tag(self, vek: bytes, sample_creds: dict):
        """Modifying the IV makes AES-GCM authentication fail."""
        encrypted_blob, iv = _encrypt(vek, sample_creds)
        iv_bytes = bytes.fromhex(iv)
        tampered = bytearray(iv_bytes)
        tampered[0] ^= 0xFF
        with pytest.raises(InvalidTag):
            decrypt_credentials(vek, encrypted_blob, tampered.hex())

    def test_returns_dict_with_expected_keys(self, vek: bytes, sample_creds: dict):
        """The decrypted result has username, password, and notes keys."""
        encrypted_blob, iv = _encrypt(vek, sample_creds)
        result = decrypt_credentials(vek, encrypted_blob, iv)
        assert "username" in result
        assert "password" in result
        assert "notes" in result

    def test_empty_notes(self, vek: bytes):
        """Credentials with empty notes are handled correctly."""
        creds = {"username": "u", "password": "p", "notes": ""}
        encrypted_blob, iv = _encrypt(vek, creds)
        result = decrypt_credentials(vek, encrypted_blob, iv)
        assert result["notes"] == ""

    def test_invalid_hex_raises_value_error(self, vek: bytes):
        """Non-hex strings for blob or IV raise ValueError."""
        with pytest.raises(ValueError):
            decrypt_credentials(vek, "nothex", "cafebabe")

        with pytest.raises(ValueError):
            decrypt_credentials(vek, "deadbeef", "nothex")


def _encrypt(key: bytes, creds: dict) -> tuple[str, str]:
    """Helper: encrypt a credential dict with AES-GCM and return (blob_hex, iv_hex)."""
    iv = os.urandom(12)
    aesgcm = AESGCM(key)
    payload = json.dumps(creds).encode("utf-8")
    ciphertext = aesgcm.encrypt(iv, payload, None)
    return ciphertext.hex(), iv.hex()