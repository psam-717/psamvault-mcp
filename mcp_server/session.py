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

# A working keyring is remembered so later tool calls do not reopen the bus.
# A failure is not: the next probe looks again, so unlocking takes effect
# without restarting the server. Tests clear both.
_KEYRING_PROBLEM: str | None = None
_KEYRING_PROBED = False

# What an agent-chosen child must not inherit. jeepney reads only
# DBUS_SESSION_BUS_ADDRESS, but GDBus (secret-tool, libsecret) falls back to
# $XDG_RUNTIME_DIR/bus when that address is unset. GNOME_KEYRING_CONTROL is the
# keyring control socket. DBUS_STARTER_* is how a bus-activated process finds
# the bus that started it. extra_env is applied after this list.
_CHILD_ENV_DENY = (
    "DBUS_SESSION_BUS_ADDRESS",
    "XDG_RUNTIME_DIR",
    "GNOME_KEYRING_CONTROL",
    "DBUS_STARTER_ADDRESS",
    "DBUS_STARTER_BUS_TYPE",
)

# libdbus autolaunches a session bus when DBUS_SESSION_BUS_ADDRESS is unset and
# DISPLAY is set (`dbus-launch --autolaunch`). Leaving the variable unset after
# stripping the real address is what opens that path. "disabled:" is the value
# libdbus and GDBus treat as "do not connect and do not autolaunch".
_NO_SESSION_BUS = "disabled:"

# load_config() copies PSAMVAULT_PEPPER into os.environ for this process.
# Session tokens stay in the keyring, but a PSAMVAULT_* name that carries one
# of these words is secret material and must not ride along in a child.
_SECRET_ENV_MARKERS = ("PEPPER", "VEK", "TOKEN", "SECRET", "PASSWORD", "SESSION", "KDF")


def _is_inherited_secret(name: str) -> bool:
    if not name.startswith("PSAMVAULT_"):
        return False
    upper = name.upper()
    return any(marker in upper for marker in _SECRET_ENV_MARKERS)


class _UninitialisedKeyring(keyring.errors.KeyringError):
    """Secret Service is up, but the default collection does not exist.

    ``keyring`` would call ``create_collection``, which opens a GUI prompt and
    blocks. This process does not create a collection.
    """


def keyring_problem() -> str | None:
    """The ``✗ … — …`` message when the OS keyring cannot be used, else None."""
    return _KEYRING_PROBLEM


def clear_keyring_problem() -> None:
    """Forget a recorded keyring failure and the remembered success."""
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
    if isinstance(exc, _UninitialisedKeyring):
        return (
            "✗ the OS keyring has no default collection — create and unlock the "
            "default keyring in your desktop session, then retry. The server will "
            "not create one, because that opens a prompt. Secrets stay in the OS "
            "keyring; there is no plaintext fallback."
        )
    if isinstance(exc, keyring.errors.KeyringLocked):
        return (
            "✗ the OS keyring is locked — unlock it and retry "
            "(Linux: Secret Service / gnome-keyring; macOS: the login keychain; "
            "Windows: Credential Manager). The server will not open an unlock prompt. "
            "Secrets stay in the OS keyring; there is no plaintext fallback."
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


def child_environment(extra: dict | None = None) -> dict[str, str]:
    """Environment for a command the agent asked the server to run.

    The session bus this process uses to reach Secret Service is removed, and
    ``DBUS_SESSION_BUS_ADDRESS`` is set to ``disabled:`` so the child cannot
    autolaunch a bus of its own. ``PSAMVAULT_PEPPER`` and other secret-shaped
    ``PSAMVAULT_*`` names are removed. ``extra`` is applied first, so an
    explicit ``extra_env`` entry is how a caller passes any of those through.
    ``DISPLAY`` stays: commands that open a window still can, and they still
    cannot autolaunch a bus.
    """
    env = os.environ.copy()
    for name in list(env):
        if name in _CHILD_ENV_DENY or _is_inherited_secret(name):
            env.pop(name, None)
    if extra:
        env.update(extra)
    if not env.get("DBUS_SESSION_BUS_ADDRESS"):
        env["DBUS_SESSION_BUS_ADDRESS"] = _NO_SESSION_BUS
    return env


def _backend_uses_secret_service(kr: object) -> bool:
    """True when ``kr`` is Secret Service, or a chainer that will call it."""
    seen: set[int] = set()

    def walk(obj: object) -> bool:
        ident = id(obj)
        if ident in seen:
            return False
        seen.add(ident)
        module = getattr(type(obj), "__module__", "") or ""
        if module.startswith("keyring.backends.SecretService"):
            return True
        backends = getattr(obj, "backends", None)
        if backends is None:
            return False
        try:
            items = list(backends)
        except Exception:
            return False
        return any(walk(item) for item in items)

    return walk(kr)


def _collection_prompt_risk() -> BaseException | None:
    """What would make the next Secret Service call open a GUI prompt.

    Reads the ``default`` alias and its ``Locked`` property. Does not call
    ``Unlock``, ``CreateCollection``, or ``get_default_collection`` (that last
    one creates the collection, which prompts). Returns None when Secret
    Service is not reachable: ``keyring`` then raises ``NoKeyringError``
    without a prompt. Returns an exception when calling ``keyring`` would
    block on a prompt; the caller must not call ``keyring`` in that case.
    """
    try:
        import secretstorage
        from secretstorage.exceptions import (
            ItemNotFoundException,
            SecretServiceNotAvailableException,
        )
    except ImportError:  # pragma: no cover - secretstorage is a dependency
        return None
    connection = None
    try:
        try:
            connection = secretstorage.dbus_init()
        except Exception:
            return None
        try:
            available = secretstorage.check_service_availability(connection)
        except SecretServiceNotAvailableException:
            return None
        if not available:
            return None
        try:
            collection = secretstorage.get_collection_by_alias(connection, "default")
        except ItemNotFoundException:
            return _UninitialisedKeyring("no default collection")
        except Exception as exc:
            return keyring.errors.KeyringLocked(
                f"could not read the default collection ({type(exc).__name__})"
            )
        try:
            locked = bool(collection.is_locked())
        except Exception as exc:
            return keyring.errors.KeyringLocked(
                f"could not read the lock state ({type(exc).__name__})"
            )
        if locked:
            return keyring.errors.KeyringLocked("the default collection is locked")
        return None
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def _prompt_risk() -> BaseException | None:
    """An exception when the next keyring call would open a GUI prompt, else None.

    None means "call keyring". That includes macOS, Windows, any backend other
    than Secret Service, a missing session bus, and a default collection that
    is already unlocked (its ``Unlock`` returns without a prompt).
    """
    adopt_session_bus()
    if os.name == "nt" or sys.platform == "darwin":
        return None
    try:
        kr = keyring.get_keyring()
    except Exception:
        return None
    if not _backend_uses_secret_service(kr):
        return None
    return _collection_prompt_risk()


def _keyring_get(username: str):
    """Read one keychain entry.

    Returns the value, None when the entry is absent, or ``_UNAVAILABLE`` when the
    keyring itself cannot be used. The failure is recorded; this function does not raise.
    """
    global _KEYRING_PROBED
    risk = _prompt_risk()
    if risk is not None:
        _record_keyring_problem(risk)
        _KEYRING_PROBED = True
        return _UNAVAILABLE
    try:
        value = keyring.get_password(_SERVICE, username)
    except Exception as exc:
        _record_keyring_problem(exc)
        _KEYRING_PROBED = True
        return _UNAVAILABLE
    _KEYRING_PROBED = True
    return value


def _keyring_delete(username: str) -> bool:
    """Delete one keychain entry. False when that would open a prompt — the caller
    must not drop the only remaining copy of the session."""
    risk = _prompt_risk()
    if risk is not None:
        _record_keyring_problem(risk)
        return False
    try:
        keyring.delete_password(_SERVICE, username)
    except keyring.errors.PasswordDeleteError:
        return True
    except Exception as exc:
        _record_keyring_problem(exc)
        return False
    return True


def _keyring_set(username: str, password: str) -> bool:
    """Store one keychain entry. False when the keyring refused — nothing is written elsewhere."""
    risk = _prompt_risk()
    if risk is not None:
        _record_keyring_problem(risk)
        return False
    try:
        keyring.set_password(_SERVICE, username, password)
    except Exception as exc:
        _record_keyring_problem(exc)
        return False
    return True


def probe_keyring() -> str | None:
    """The ``✗`` message if the OS keyring cannot be used, else None.

    A missing entry is a working keyring. A working keyring is remembered so later
    tool calls do not reopen the session bus. A failure is not remembered: the next
    call looks again, so unlocking or creating the default collection takes effect
    without restarting the server.
    """
    global _KEYRING_PROBLEM, _KEYRING_PROBED
    if _KEYRING_PROBED and _KEYRING_PROBLEM is None:
        return None
    _KEYRING_PROBLEM = None
    _KEYRING_PROBED = False
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
    if not _keyring_set("session.access_token", access_token) or not _keyring_set(
        "session.refresh_token", refresh_token
    ):
        raise RuntimeError(keyring_problem() or explain_keyring_failure(
            keyring.errors.KeyringLocked("unavailable")
        ))


def update_access_token(access_token: str) -> None:
    """
    Overwrite just the access_token in the keychain.
    Called automatically after a successful token refresh.
    """
    if not _keyring_set("session.access_token", access_token):
        raise RuntimeError(keyring_problem() or explain_keyring_failure(
            keyring.errors.KeyringLocked("unavailable")
        ))


def clear_session() -> None:
    """
    Delete all session data from the keychain and remove the presence marker.
    A locked keyring leaves the marker in place: deleting it would drop the
    only sign that a session exists, and the delete itself would prompt.
    """
    for key in _SESSION_KEYS:
        if not _keyring_delete(key):
            return
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()


# Move a legacy plaintext session into the keychain as soon as this module is imported.
# A missing or locked keyring must not raise here: importing the package is what
# `--version`, `--help`, and the upgrade smoke test do.
try:
    _migrate_legacy_session()
except Exception as exc:  # pragma: no cover - _keyring_set already catches keyring failures
    _record_keyring_problem(exc)
