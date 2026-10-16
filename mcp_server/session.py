import json
import os
import stat
import sys
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

# A keyring read that failed. Distinct from None, which means "no entry".
class _Unavailable:
    pass


_UNAVAILABLE = _Unavailable()

# First failure in this process. Cleared only by tests.
_KEYRING_PROBLEM: str | None = None
_KEYRING_PROBED = False


def keyring_problem() -> str | None:
    """The ``✗ … — …`` message when the OS keyring cannot be used, else None."""
    return _KEYRING_PROBLEM


def clear_keyring_problem() -> None:
    """Forget a recorded keyring failure. Tests only — a server process keeps the first one."""
    global _KEYRING_PROBLEM, _KEYRING_PROBED
    _KEYRING_PROBLEM = None
    _KEYRING_PROBED = False


def user_facing_keyring_error(exc: BaseException) -> str | None:
    """The message to show for a keyring failure. None when ``exc`` is something else."""
    if isinstance(exc, keyring.errors.KeyringError) or str(exc).startswith("✗ "):
        return str(exc) if str(exc).startswith("✗ ") else explain_keyring_failure(exc)
    return None


def explain_keyring_failure(exc: BaseException) -> str:
    """``✗ <what failed> — <what to do next>``. Never recommends plaintext storage."""
    if isinstance(exc, keyring.errors.KeyringLocked):
        return (
            "✗ the OS keyring is locked — unlock it and retry "
            "(Linux: Secret Service / gnome-keyring; macOS: the login keychain; "
            "Windows: Credential Manager). Secrets stay in the OS keyring; "
            "there is no plaintext fallback."
        )
    if sys.platform.startswith("linux"):
        bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        if not bus:
            return (
                "✗ no OS keyring reachable — DBUS_SESSION_BUS_ADDRESS is not set and "
                f"{_standard_bus_socket()} is not a usable session-bus socket, so Secret Service "
                "cannot be reached. Start a Secret Service (gnome-keyring) or pass "
                "DBUS_SESSION_BUS_ADDRESS through the MCP client environment. "
                "Secrets stay in the OS keyring; there is no plaintext fallback."
            )
        return (
            "✗ no OS keyring reachable — Secret Service did not answer on the session bus "
            f"({bus}). Start and unlock a Secret Service provider (gnome-keyring). "
            "Secrets stay in the OS keyring; there is no plaintext fallback."
        )
    if sys.platform == "darwin":
        return (
            "✗ no OS keyring reachable — the macOS login keychain did not answer. "
            "Unlock it in Keychain Access and retry. Secrets stay in the OS keyring; "
            "there is no plaintext fallback."
        )
    if os.name == "nt":
        return (
            "✗ no OS keyring reachable — Windows Credential Manager did not answer. "
            "Confirm this process is the logged-in user and retry. "
            "Secrets stay in the OS keyring; there is no plaintext fallback."
        )
    return (
        f"✗ the OS keyring failed ({type(exc).__name__}) — restore a working OS keyring and retry. "
        "Secrets stay in the OS keyring; there is no plaintext fallback."
    )


def _record_keyring_problem(exc: BaseException) -> str:
    global _KEYRING_PROBLEM
    message = explain_keyring_failure(exc)
    if _KEYRING_PROBLEM is None:
        _KEYRING_PROBLEM = message
    return _KEYRING_PROBLEM


def _standard_bus_socket() -> Path:
    try:
        uid = os.getuid()
    except AttributeError:  # pragma: no cover - Windows
        return Path("/run/user/0/bus")
    return Path(f"/run/user/{uid}/bus")


def runtime_dir_candidates() -> list[Path]:
    """Directories that may hold this user's session bus. Empty off Linux.

    jeepney (what keyring's Secret Service backend uses) reads only
    ``DBUS_SESSION_BUS_ADDRESS``. These directories are how we find that socket
    when an MCP host started us without it.
    """
    if os.name == "nt" or sys.platform == "darwin":
        return []
    found: list[Path] = []
    env_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if env_runtime:
        found.append(Path(env_runtime))
    standard = _standard_bus_socket().parent
    if standard not in found:
        found.append(standard)
    return found


def _usable_bus_socket(path: Path) -> bool:
    """True when ``path`` is a socket owned by this user. A regular file is not a bus."""
    try:
        st = path.stat()
    except OSError:
        return False
    if not stat.S_ISSOCK(st.st_mode):
        return False
    try:
        return st.st_uid == os.getuid()
    except AttributeError:  # pragma: no cover - Windows
        return False


def adopt_session_bus() -> list[str]:
    """Point an unset Linux session bus at the per-user socket, when that socket exists.

    MCP hosts that follow the official SDK spawn the server with a minimal environment
    (``HOME``, ``LOGNAME``, ``PATH``, ``SHELL``, ``TERM``, ``USER`` on POSIX) and then
    overlay ``env``. ``PYTHONPATH=""`` therefore arrives without ``DBUS_SESSION_BUS_ADDRESS``,
    and jeepney 0.9 cannot open Secret Service. Adopting the standard socket does not
    choose a backend and does not store a secret. An explicit value is never replaced.
    Off Linux this is a no-op.

    Returns the names of the variables that were set.
    """
    if os.name == "nt" or sys.platform == "darwin":
        return []
    adopted: list[str] = []
    if not os.environ.get("XDG_RUNTIME_DIR"):
        for candidate in runtime_dir_candidates():
            try:
                if candidate.is_dir():
                    os.environ["XDG_RUNTIME_DIR"] = str(candidate)
                    adopted.append("XDG_RUNTIME_DIR")
                    break
            except OSError:
                continue
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        sockets: list[Path] = []
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if runtime:
            sockets.append(Path(runtime) / "bus")
        for candidate in runtime_dir_candidates():
            sock = candidate / "bus"
            if sock not in sockets:
                sockets.append(sock)
        for sock in sockets:
            if _usable_bus_socket(sock):
                os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={sock}"
                adopted.append("DBUS_SESSION_BUS_ADDRESS")
                break
    return adopted


def _keyring_get(username: str):
    """Read one keychain entry.

    Returns the value, None when the entry is absent, or ``_UNAVAILABLE`` when the
    keyring itself cannot be used. The failure is recorded; this function does not raise.
    """
    global _KEYRING_PROBED
    adopt_session_bus()
    try:
        value = keyring.get_password(_SERVICE, username)
    except Exception as exc:
        _record_keyring_problem(exc)
        _KEYRING_PROBED = True
        return _UNAVAILABLE
    _KEYRING_PROBED = True
    return value


def _keyring_set(username: str, password: str) -> bool:
    """Store one keychain entry. False when the keyring refused — nothing is written elsewhere."""
    adopt_session_bus()
    try:
        keyring.set_password(_SERVICE, username, password)
    except Exception as exc:
        _record_keyring_problem(exc)
        return False
    return True


def probe_keyring() -> str | None:
    """The ``✗`` message if the OS keyring cannot be used, else None.

    A missing entry is a working keyring. The result of the first probe is reused so
    every tool call does not open the session bus again.
    """
    global _KEYRING_PROBED
    if _KEYRING_PROBLEM is not None:
        return _KEYRING_PROBLEM
    if _KEYRING_PROBED:
        return None
    _keyring_get(_PEPPER_KEY)
    _KEYRING_PROBED = True
    return _KEYRING_PROBLEM


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
            # Migration: pepper in config.env → move to keychain.
            # If the keyring cannot store it, the line stays in the file and the value is
            # NOT copied into the environment. Plaintext is not a fallback.
            if key == "PSAMVAULT_PEPPER":
                existing = _keyring_get(_PEPPER_KEY)
                if existing is _UNAVAILABLE:
                    continue
                if existing:
                    filtered[i] = None
                    continue
                if _keyring_set(_PEPPER_KEY, value):
                    filtered[i] = None
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

    # Load pepper from keychain into os.environ. A failure here is recorded and
    # does not raise: --version, startup, and the upgrade smoke import must survive
    # a host with no Secret Service.
    pepper = _keyring_get(_PEPPER_KEY)
    if isinstance(pepper, str) and pepper and "PSAMVAULT_PEPPER" not in os.environ:
        os.environ["PSAMVAULT_PEPPER"] = pepper


def _get_keychain(key: str) -> str:
    """
    Read a value from the OS keychain under the psamvault service.

    Raises:
        RuntimeError: If the key is missing — the user needs to log in again —
            or if the OS keyring itself cannot be used. The latter message starts
            with ``✗`` and does not suggest storing the secret in a file.
    """
    value = _keyring_get(key)
    if value is _UNAVAILABLE:
        raise RuntimeError(keyring_problem() or explain_keyring_failure(
            keyring.errors.NoKeyringError("unavailable")
        ))
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
        if field in old_data and not _keyring_set(keychain_key, old_data[field]):
            # Leave the file in place. Clearing it after a failed write would drop
            # the only copy of the session.
            return

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
    value = _keyring_get("session.access_token")
    if value is _UNAVAILABLE:
        return False
    return value is not None


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


# Move a legacy plaintext session into the keychain as soon as this module is imported.
# A missing or locked keyring must not raise here: importing the package is what
# `--version`, `--help`, and the upgrade smoke test do.
try:
    _migrate_legacy_session()
except Exception as exc:  # pragma: no cover - _keyring_set already catches keyring failures
    _record_keyring_problem(exc)
