"""Tests for session.py — keychain-based session management."""

import json
from pathlib import Path

import keyring
import pytest

from mcp_server.session import (
    SESSION_FILE,
    _SESSION_KEYS,
    _get_keychain,
    _migrate_legacy_session,
    clear_session,
    get_access_token,
    get_refresh_token,
    get_vek,
    is_logged_in,
    update_access_token,
    update_tokens,
)

_SERVICE = "psamvault"


class TestIsLoggedIn:
    def test_not_logged_in_when_file_missing(self):
        """is_logged_in returns False when session.json does not exist."""
        assert not is_logged_in()

    def test_not_logged_in_when_keychain_missing(self, session_file: Path):
        """File exists but keychain entry is missing."""
        keyring.delete_password(_SERVICE, "session.access_token")
        assert not is_logged_in()

    def test_logged_in_when_file_and_keychain_exist(self, session_file: Path):
        """Both file and keychain present."""
        assert is_logged_in()


class TestGetAccessToken:
    def test_returns_token(self, session_file: Path):
        keyring.set_password(_SERVICE, "session.access_token", "mytoken")
        assert get_access_token() == "mytoken"

    def test_raises_when_missing(self):
        with pytest.raises(RuntimeError, match="not found in keychain"):
            get_access_token()


class TestGetRefreshToken:
    def test_returns_token(self, session_file: Path):
        keyring.set_password(_SERVICE, "session.refresh_token", "myrefresh")
        assert get_refresh_token() == "myrefresh"

    def test_raises_when_missing(self):
        with pytest.raises(RuntimeError, match="not found in keychain"):
            get_refresh_token()


class TestGetVek:
    def test_returns_bytes(self, session_file: Path):
        keyring.set_password(_SERVICE, "session.vek", "abcd" * 8)  # 32 hex chars = 16 bytes
        vek = get_vek()
        assert isinstance(vek, bytes)
        assert len(vek) == 16

    def test_raises_when_missing(self):
        with pytest.raises(RuntimeError, match="not found in keychain"):
            get_vek()


class TestUpdateTokens:
    def test_overwrites_both_tokens(self, session_file: Path):
        update_tokens("new_access", "new_refresh")
        assert keyring.get_password(_SERVICE, "session.access_token") == "new_access"
        assert keyring.get_password(_SERVICE, "session.refresh_token") == "new_refresh"


class TestUpdateAccessToken:
    def test_overwrites_access_token_only(self, session_file: Path):
        keyring.set_password(_SERVICE, "session.access_token", "old")
        update_access_token("new_access")
        assert keyring.get_password(_SERVICE, "session.access_token") == "new_access"


class TestClearSession:
    def test_clears_all_keys_and_file(self, session_file: Path):
        clear_session()
        for key in _SESSION_KEYS:
            assert keyring.get_password(_SERVICE, key) is None
        assert not session_file.exists()


class TestMigrateLegacySession:
    def test_plaintext_file_migrated_to_keychain(self, tmp_path: Path):
        """Old-format session.json with plaintext fields moves to keychain."""
        session_dir = tmp_path / ".psamvault"
        session_dir.mkdir(parents=True)
        sf = session_dir / "session.json"
        old_data = {
            "access_token": "legacy_token",
            "refresh_token": "legacy_refresh",
            "kdf_salt": "legacy_salt",
            "vek": "deadbeef",
            "encrypted_vek": "cafe",
            "vek_iv": "babe",
        }
        sf.write_text(json.dumps(old_data))

        # Override SESSION_FILE for the migration
        import mcp_server.session as s

        original = s.SESSION_FILE
        s.SESSION_FILE = sf
        try:
            _migrate_legacy_session()
        finally:
            s.SESSION_FILE = original

        # Fields should now be in keychain
        assert keyring.get_password(_SERVICE, "session.access_token") == "legacy_token"
        assert keyring.get_password(_SERVICE, "session.refresh_token") == "legacy_refresh"
        assert keyring.get_password(_SERVICE, "session.kdf_salt") == "legacy_salt"
        assert keyring.get_password(_SERVICE, "session.vek") == "deadbeef"
        assert keyring.get_password(_SERVICE, "session.encrypted_vek") == "cafe"
        assert keyring.get_password(_SERVICE, "session.vek_iv") == "babe"

        # File should now be empty marker
        assert sf.read_text() == "{}"

    def test_empty_file_does_nothing(self, tmp_path: Path):
        sf = tmp_path / "session.json"
        sf.write_text("{}")

        import mcp_server.session as s

        original = s.SESSION_FILE
        s.SESSION_FILE = sf
        try:
            _migrate_legacy_session()  # should not raise
        finally:
            s.SESSION_FILE = original

    def test_missing_file_does_nothing(self):
        _migrate_legacy_session()  # should not raise


class TestGetKeychain:
    def test_raises_runtime_error_on_missing(self):
        with pytest.raises(RuntimeError, match="not found in keychain"):
            _get_keychain("session.nonexistent")