"""Shared test fixtures and configuration for psamvault-mcp tests."""

import os

# Keyring backend must be set before any keyring import
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
os.environ.setdefault("PSAMVAULT_PEPPER", "a" * 64)
os.environ.setdefault("PSAMVAULT_API_URL", "https://psamvault-test.example.com")

import json
from pathlib import Path

import keyring
import pytest

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ── In-memory keyring mock ────────────────────────────────────────────────────
# The null keyring backend doesn't reliably persist across all Python runtimes
# and pytest configurations. We monkeypatch the keyring module at import time so
# all modules (session.py, test files) use the in-memory store automatically.

_KEYRING_STORE: dict[str, str] = {}


def _mock_set_password(service: str, key: str, password: str) -> None:
    _KEYRING_STORE[f"{service}:{key}"] = password


def _mock_get_password(service: str, key: str) -> str | None:
    return _KEYRING_STORE.get(f"{service}:{key}")


def _mock_delete_password(service: str, key: str) -> None:
    _KEYRING_STORE.pop(f"{service}:{key}", None)


# Apply patches at module level (before any test or fixture runs).
keyring.set_password = _mock_set_password
keyring.get_password = _mock_get_password
keyring.delete_password = _mock_delete_password

# Redirect ~/.psamvault to a temp directory so module-level _migrate_legacy_session()
# in session.py never touches the real user config.
import tempfile as _tempfile
_TEMP_HOME = Path(_tempfile.mkdtemp(prefix="psamvault_test_"))
_TEMP_PSAMVAULT = _TEMP_HOME / ".psamvault"
_TEMP_PSAMVAULT.mkdir(parents=True, exist_ok=True)
(_TEMP_PSAMVAULT / "session.json").write_text("{}")
Path.home = lambda: _TEMP_HOME


# ── Shared test constants ──────────────────────────────────────────────────────

TEST_VEK = bytes(range(32))  # deterministic 32-byte VEK
TEST_ACCESS_TOKEN = "test_access_token_abc123"
TEST_REFRESH_TOKEN = "test_refresh_token_xyz789"
TEST_KDF_SALT = "ab" * 16  # 32 hex bytes
TEST_SITE = "github.com"
TEST_USERNAME = "testuser"
TEST_PASSWORD = "testpass123"
TEST_CREDS = {"username": TEST_USERNAME, "password": TEST_PASSWORD, "notes": "test notes"}

_SERVICE = "psamvault"
_SESSION_KEYS = [
    "session.access_token",
    "session.refresh_token",
    "session.kdf_salt",
    "session.vek",
    "session.encrypted_vek",
    "session.vek_iv",
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def encrypt_test_creds() -> tuple[str, str]:
    """Encrypt TEST_CREDS with TEST_VEK for use in API responses.

    Returns (encrypted_blob_hex, iv_hex).
    """
    iv = os.urandom(12)
    aesgcm = AESGCM(TEST_VEK)
    payload = json.dumps(TEST_CREDS).encode("utf-8")
    ciphertext = aesgcm.encrypt(iv, payload, None)
    return ciphertext.hex(), iv.hex()


def write_keychain_entries() -> None:
    """Write all test session keys into the null keyring backend."""
    entries = {
        "session.access_token": TEST_ACCESS_TOKEN,
        "session.refresh_token": TEST_REFRESH_TOKEN,
        "session.kdf_salt": TEST_KDF_SALT,
        "session.vek": TEST_VEK.hex(),
        "session.encrypted_vek": "deadbeef",
        "session.vek_iv": "cafebabe",
    }
    for key, value in entries.items():
        keyring.set_password(_SERVICE, key, value)


def clear_keychain_entries() -> None:
    """Remove all test session keys from the null keyring backend."""
    for key in _SESSION_KEYS:
        try:
            keyring.delete_password(_SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            pass


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def vek() -> bytes:
    """Return the deterministic test VEK."""
    return TEST_VEK


@pytest.fixture
def sample_creds() -> dict:
    """Return sample decrypted credential data."""
    return dict(TEST_CREDS)


@pytest.fixture
def encrypted_entry() -> tuple[str, str]:
    """Return a pre-encrypted vault entry (blob_hex, iv_hex)."""
    return encrypt_test_creds()


@pytest.fixture
def session_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temporary session.json presence marker and return its path.

    Also writes session keys into the in-memory keyring, and patches
    mcp_server.session.SESSION_FILE to point at the temp file.
    Cleans up keychain entries after the test.
    """
    import mcp_server.session

    session_dir = tmp_path / ".psamvault"
    session_dir.mkdir(parents=True)
    sf = session_dir / "session.json"
    sf.write_text("{}")

    write_keychain_entries()

    monkeypatch.setattr(mcp_server.session, "SESSION_FILE", sf)

    yield sf

    clear_keychain_entries()


@pytest.fixture
def mock_session(session_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set up a fully mocked session environment.

    Overrides SESSION_FILE and Path.home() so tests don't touch the real
    ~/.psamvault directory. Writes keychain entries so session functions
    work naturally.
    """
    monkeypatch.setattr("mcp_server.session.SESSION_FILE", session_file)
    monkeypatch.setattr(Path, "home", lambda: session_file.parent.parent)

    yield

    clear_keychain_entries()


@pytest.fixture
def mock_consent_approved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make consent.request_consent return True without showing a dialog."""
    import mcp_server.consent
    monkeypatch.setattr(mcp_server.consent, "request_consent", lambda *a, **kw: True)


@pytest.fixture
def mock_tool_deps(mock_session, mock_consent_approved, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set up all dependencies needed by tools.py tests.

    Combines mock_session + mock_consent_approved, and patches the
    references that tools.py imports from session and consent modules
    so they point to the mocked versions.
    """
    import mcp_server.tools

    monkeypatch.setattr(mcp_server.tools, "is_logged_in", lambda: True)
    monkeypatch.setattr(mcp_server.tools, "get_access_token", lambda: TEST_ACCESS_TOKEN)
    monkeypatch.setattr(mcp_server.tools, "get_vek", lambda: TEST_VEK)

    # Async mocks — the real functions are async def, so lambdas would fail when awaited
    async def _mock_decrypt(site):
        return dict(TEST_CREDS)

    async def _mock_consent(*a, **kw):
        return True

    monkeypatch.setattr(
        mcp_server.tools, "_decrypt_site_credential",
        _mock_decrypt,
    )

    monkeypatch.setattr(
        mcp_server.tools, "_request_consent_async",
        _mock_consent,
    )

    yield