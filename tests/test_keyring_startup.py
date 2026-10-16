"""Keyring-less startup and the Linux session bus.

`--version`, `--help`, and the upgrade smoke import must not crash when the OS
keyring is missing, locked, or reachable only through a session bus the MCP host
stripped out of the environment. Secrets are never copied out of a file to stand
in for that keyring.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import keyring
import pytest

from mcp_server import session
from mcp_server.cmd_runner import run_command_with_credential
from mcp_server.main import handle_call_tool
from mcp_server.upgrade_safety import SMOKE_CODE
from mcp_server.version_check import SKIP_UPDATE_CHECK_ENV

_ENV_KEYS = ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR")


@pytest.fixture(autouse=True)
def _isolated_keyring_state():
    """A failure recorded in one test must not answer the next test's probe."""
    saved = {key: os.environ.get(key) for key in _ENV_KEYS}
    session.clear_keyring_problem()
    yield
    session.clear_keyring_problem()
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _raise_nokeyring(service, username, password=None):
    raise keyring.errors.NoKeyringError("no backend")


def _raise_locked(service, username, password=None):
    raise keyring.errors.KeyringLocked("locked")


def _child_env(home: Path, backend: str) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PYTHON_KEYRING_BACKEND"] = backend
    env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    env.pop("XDG_RUNTIME_DIR", None)
    env[SKIP_UPDATE_CHECK_ENV] = "1"
    return env


# ── messages ───────────────────────────────────────────────────────────────────
def test_missing_bus_message_names_the_variable_and_refuses_plaintext(monkeypatch):
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    text = session.explain_keyring_failure(keyring.errors.NoKeyringError("no backend"))
    assert text.startswith("✗ no OS keyring reachable — ")
    assert "DBUS_SESSION_BUS_ADDRESS" in text
    assert "no plaintext fallback" in text
    assert "keyrings.alt" not in text


def test_bus_set_but_secret_service_silent(monkeypatch):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1/bus")
    text = session.explain_keyring_failure(keyring.errors.NoKeyringError("no backend"))
    assert text.startswith("✗ no OS keyring reachable — ")
    assert "did not answer" in text
    assert "no plaintext fallback" in text


def test_locked_keyring_message():
    text = session.explain_keyring_failure(keyring.errors.KeyringLocked("locked"))
    assert text.startswith("✗ the OS keyring is locked — ")
    assert "no plaintext fallback" in text


def test_macos_and_windows_messages_do_not_mention_dbus(monkeypatch):
    monkeypatch.setattr(session.sys, "platform", "darwin")
    mac = session.explain_keyring_failure(keyring.errors.NoKeyringError("no"))
    assert mac.startswith("✗ no OS keyring reachable — ")
    assert "DBUS_SESSION_BUS_ADDRESS" not in mac
    monkeypatch.setattr(session.sys, "platform", "win32")
    monkeypatch.setattr(session.os, "name", "nt")
    windows = session.explain_keyring_failure(keyring.errors.NoKeyringError("no"))
    assert windows.startswith("✗ no OS keyring reachable — ")
    assert "Credential Manager" in windows
    assert "DBUS_SESSION_BUS_ADDRESS" not in windows


# ── load_config: working, missing, locked; plaintext is not a fallback ─────────
def test_working_keyring_still_loads_the_pepper(monkeypatch):
    monkeypatch.setattr(session, "adopt_session_bus", lambda: [])
    monkeypatch.delenv("PSAMVAULT_PEPPER", raising=False)
    monkeypatch.setattr(keyring, "get_password", lambda service, username: "pepper-from-keyring")
    session.load_config()
    assert os.environ["PSAMVAULT_PEPPER"] == "pepper-from-keyring"
    assert session.keyring_problem() is None


def test_missing_keyring_does_not_raise_or_copy_a_file_pepper(monkeypatch):
    monkeypatch.setattr(session, "adopt_session_bus", lambda: [])
    monkeypatch.delenv("PSAMVAULT_PEPPER", raising=False)
    monkeypatch.delenv("PSAMVAULT_API_URL", raising=False)
    session.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    session.CONFIG_FILE.write_text(
        "PSAMVAULT_PEPPER=plaintext-pepper-should-stay\n"
        "PSAMVAULT_API_URL=https://api.example.com\n"
    )
    monkeypatch.setattr(keyring, "get_password", _raise_nokeyring)
    monkeypatch.setattr(keyring, "set_password", _raise_nokeyring)

    session.load_config()  # must not raise

    assert os.environ.get("PSAMVAULT_PEPPER") != "plaintext-pepper-should-stay"
    assert "plaintext-pepper-should-stay" in session.CONFIG_FILE.read_text()
    assert os.environ["PSAMVAULT_API_URL"] == "https://api.example.com"
    problem = session.keyring_problem()
    assert problem and problem.startswith("✗ ") and "—" in problem


def test_locked_keyring_is_reported_and_leaves_the_pepper_file(monkeypatch):
    monkeypatch.setattr(session, "adopt_session_bus", lambda: [])
    monkeypatch.delenv("PSAMVAULT_PEPPER", raising=False)
    session.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    session.CONFIG_FILE.write_text("PSAMVAULT_PEPPER=still-in-the-file\n")
    monkeypatch.setattr(keyring, "get_password", _raise_locked)
    monkeypatch.setattr(keyring, "set_password", _raise_locked)

    session.load_config()

    assert "still-in-the-file" in session.CONFIG_FILE.read_text()
    assert os.environ.get("PSAMVAULT_PEPPER") != "still-in-the-file"
    assert session.keyring_problem().startswith("✗ the OS keyring is locked — ")


def test_working_keyring_still_migrates_a_file_pepper(monkeypatch):
    monkeypatch.setattr(session, "adopt_session_bus", lambda: [])
    monkeypatch.delenv("PSAMVAULT_PEPPER", raising=False)
    store: dict[str, str] = {}

    def _get(service, username):
        return store.get(username)

    def _set(service, username, password):
        store[username] = password

    monkeypatch.setattr(keyring, "get_password", _get)
    monkeypatch.setattr(keyring, "set_password", _set)
    session.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    session.CONFIG_FILE.write_text("PSAMVAULT_PEPPER=migrate-me\n")

    session.load_config()

    assert store["config.pepper"] == "migrate-me"
    assert "PSAMVAULT_PEPPER" not in session.CONFIG_FILE.read_text()
    assert os.environ["PSAMVAULT_PEPPER"] == "migrate-me"
    assert session.keyring_problem() is None


def test_legacy_session_is_not_wiped_when_the_keyring_cannot_store_it(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "adopt_session_bus", lambda: [])
    session_file = tmp_path / "session.json"
    session_file.write_text(json.dumps({"access_token": "legacy-token", "vek": "ab"}))
    monkeypatch.setattr(session, "SESSION_FILE", session_file)
    monkeypatch.setattr(keyring, "set_password", _raise_nokeyring)

    session._migrate_legacy_session()

    kept = json.loads(session_file.read_text())
    assert kept["access_token"] == "legacy-token"
    assert session.keyring_problem().startswith("✗ ")


def test_get_keychain_raises_the_actionable_message_when_locked(monkeypatch):
    monkeypatch.setattr(session, "adopt_session_bus", lambda: [])
    monkeypatch.setattr(keyring, "get_password", _raise_locked)
    with pytest.raises(RuntimeError, match="✗ the OS keyring is locked — "):
        session.get_access_token()


# ── tools: the message, not a traceback; login is a different case ─────────────
def test_vault_tool_returns_the_keyring_message(monkeypatch):
    monkeypatch.setattr(session, "adopt_session_bus", lambda: [])
    monkeypatch.setattr(keyring, "get_password", _raise_nokeyring)
    session.load_config()

    vault = asyncio.run(handle_call_tool("list_vault_sites", {}))
    payload = json.loads(vault[0].text)
    assert payload["error"].startswith("✗ ")
    assert "Traceback" not in vault[0].text
    assert "keyrings.alt" not in vault[0].text

    version = json.loads(asyncio.run(handle_call_tool("get_version", {}))[0].text)
    assert "version" in version
    assert "error" not in version


def test_working_keyring_without_a_session_still_says_login(monkeypatch):
    monkeypatch.setattr(session, "adopt_session_bus", lambda: [])
    monkeypatch.setattr(keyring, "get_password", lambda service, username: None)
    session.load_config()
    assert session.keyring_problem() is None

    payload = json.loads(asyncio.run(handle_call_tool("list_vault_sites", {}))[0].text)
    assert "psamvault login" in payload["error"]
    assert not payload["error"].startswith("✗ ")


# ── Linux session bus adoption ─────────────────────────────────────────────────
def _bind_socket(path: Path) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX)
    sock.bind(str(path))
    return sock


def test_adopts_the_user_bus_when_the_variables_are_unset(monkeypatch, tmp_path):
    sock = _bind_socket(tmp_path / "bus")
    try:
        monkeypatch.setattr(session, "runtime_dir_candidates", lambda: [tmp_path])
        monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        adopted = session.adopt_session_bus()
    finally:
        sock.close()
    assert adopted == ["XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"]
    assert os.environ["XDG_RUNTIME_DIR"] == str(tmp_path)
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={tmp_path / 'bus'}"


def test_does_not_override_an_explicit_bus_address(monkeypatch, tmp_path):
    sock = _bind_socket(tmp_path / "bus")
    try:
        monkeypatch.setattr(session, "runtime_dir_candidates", lambda: [tmp_path])
        monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/already/set")
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        session.adopt_session_bus()
    finally:
        sock.close()
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/already/set"


def test_a_regular_file_is_not_a_session_bus(monkeypatch, tmp_path):
    (tmp_path / "bus").write_text("not a socket")
    monkeypatch.setattr(session, "runtime_dir_candidates", lambda: [tmp_path])
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    adopted = session.adopt_session_bus()
    assert "DBUS_SESSION_BUS_ADDRESS" not in adopted
    assert "DBUS_SESSION_BUS_ADDRESS" not in os.environ


def test_does_not_adopt_another_users_socket(monkeypatch, tmp_path):
    sock = _bind_socket(tmp_path / "bus")
    someone_else = os.getuid() + 1
    try:
        monkeypatch.setattr(session, "runtime_dir_candidates", lambda: [tmp_path])
        monkeypatch.setattr(session.os, "getuid", lambda: someone_else)
        monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        adopted = session.adopt_session_bus()
    finally:
        sock.close()
    assert adopted == []
    assert "DBUS_SESSION_BUS_ADDRESS" not in os.environ


def test_adoption_is_a_noop_off_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(session.sys, "platform", "darwin")
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    assert session.adopt_session_bus() == []
    assert session.runtime_dir_candidates() == []
    monkeypatch.setattr(session.sys, "platform", "win32")
    monkeypatch.setattr(session.os, "name", "nt")
    assert session.adopt_session_bus() == []


def test_missing_socket_leaves_the_environment_alone(monkeypatch, tmp_path):
    empty = tmp_path / "runtime"
    empty.mkdir()
    monkeypatch.setattr(session, "runtime_dir_candidates", lambda: [empty])
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    adopted = session.adopt_session_bus()
    # The runtime directory itself may be recorded; the bus address must not be invented.
    assert "DBUS_SESSION_BUS_ADDRESS" not in adopted
    assert "DBUS_SESSION_BUS_ADDRESS" not in os.environ


# ── subprocess: the documented smoke test, against a backend that raises ──────
def _run(args: list[str], home: Path, backend: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=home,
        env=_child_env(home, backend),
        capture_output=True,
        text=True,
        timeout=30,
        **kwargs,
    )


def test_version_and_help_do_not_touch_the_keyring(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    marker = tmp_path / "touched"
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    (backend_dir / "probe_backend.py").write_text(
        "from keyring.backend import KeyringBackend\n"
        "from keyring.errors import NoKeyringError\n"
        f"_MARKER = {str(marker)!r}\n"
        "class Keyring(KeyringBackend):\n"
        "    priority = 1\n"
        "    def _touch(self, kind):\n"
        "        open(_MARKER, 'a', encoding='utf-8').write(kind + '\\n')\n"
        "        raise NoKeyringError('probe')\n"
        "    def get_password(self, service, username):\n"
        "        self._touch('get')\n"
        "    def set_password(self, service, username, password):\n"
        "        self._touch('set')\n"
        "    def delete_password(self, service, username):\n"
        "        self._touch('del')\n"
    )
    env = _child_env(home, "probe_backend.Keyring")
    env["PYTHONPATH"] = str(backend_dir)

    version = subprocess.run(
        [sys.executable, "-m", "mcp_server.main", "--version"],
        cwd=home, env=env, capture_output=True, text=True, timeout=30,
    )
    help_proc = subprocess.run(
        [sys.executable, "-m", "mcp_server.main", "--help"],
        cwd=home, env=env, capture_output=True, text=True, timeout=30,
    )

    assert version.returncode == 0, version.stderr
    assert version.stdout.strip()
    assert "Traceback" not in version.stderr
    assert help_proc.returncode == 0, help_proc.stderr
    assert "Usage:" in help_proc.stdout
    assert "Traceback" not in help_proc.stderr
    assert not marker.exists(), marker.read_text() if marker.exists() else ""


def test_smoke_import_survives_a_missing_keyring(tmp_path):
    """The same import `compat --apply` uses to decide whether to roll an upgrade back."""
    home = tmp_path / "home"
    home.mkdir()
    proc = _run([sys.executable, "-c", SMOKE_CODE], home, "keyring.backends.fail.Keyring")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["version"]
    assert payload["tools"]
    assert "Traceback" not in proc.stderr
    assert "NoKeyringError" not in proc.stderr


def test_server_startup_warns_instead_of_crashing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    proc = _run(
        [sys.executable, "-c", "from mcp_server.main import main; main()"],
        home,
        "keyring.backends.fail.Keyring",
        input="",
    )
    assert "Traceback" not in proc.stderr
    assert "NoKeyringError" not in proc.stderr
    assert "keyrings.alt" not in proc.stderr
    assert "✗ no OS keyring reachable — " in proc.stderr


# ── locked / uninitialised: never Unlock, never CreateCollection ──────────────
def test_collection_inspection_does_not_unlock_or_create(monkeypatch):
    """The call that used to hang is collection.unlock / create_collection."""
    import secretstorage

    events: list[object] = []

    class Conn:
        def close(self):
            events.append("close")

    class Coll:
        def is_locked(self):
            events.append("is_locked")
            return True

        def unlock(self):
            events.append("unlock")

    def dbus_init():
        events.append("dbus_init")
        return Conn()

    def get_alias(connection, alias):
        events.append(("alias", alias))
        return Coll()

    def banned(*_a, **_k):
        events.append("banned")
        raise AssertionError("this call opens a GUI prompt")

    monkeypatch.setattr(secretstorage, "dbus_init", dbus_init)
    monkeypatch.setattr(secretstorage, "check_service_availability", lambda _c: True)
    monkeypatch.setattr(secretstorage, "get_collection_by_alias", get_alias)
    monkeypatch.setattr(secretstorage, "get_default_collection", banned)
    monkeypatch.setattr(secretstorage, "create_collection", banned)

    risk = session._collection_prompt_risk()

    assert isinstance(risk, keyring.errors.KeyringLocked)
    assert not isinstance(risk, session._UninitialisedKeyring)
    assert "unlock" not in events
    assert "banned" not in events
    assert ("alias", "default") in events
    assert "close" in events
    text = session.explain_keyring_failure(risk)
    assert text.startswith("✗ the OS keyring is locked — ")
    assert "will not open an unlock prompt" in text


def test_a_missing_default_collection_is_not_created(monkeypatch):
    import secretstorage
    from secretstorage.exceptions import ItemNotFoundException

    events: list[object] = []

    class Conn:
        def close(self):
            events.append("close")

    def banned(*_a, **_k):
        events.append("banned")
        raise AssertionError("create_collection opens a prompt")

    monkeypatch.setattr(secretstorage, "dbus_init", lambda: Conn())
    monkeypatch.setattr(secretstorage, "check_service_availability", lambda _c: True)
    def missing(_connection, _alias):
        raise ItemNotFoundException("no default alias")

    monkeypatch.setattr(secretstorage, "get_collection_by_alias", missing)
    monkeypatch.setattr(secretstorage, "get_default_collection", banned)
    monkeypatch.setattr(secretstorage, "create_collection", banned)

    risk = session._collection_prompt_risk()

    assert isinstance(risk, session._UninitialisedKeyring)
    assert "banned" not in events
    assert "close" in events
    text = session.explain_keyring_failure(risk)
    assert text.startswith("✗ the OS keyring has no default collection — ")
    assert "will not create one" in text
    assert "no plaintext fallback" in text


def test_an_unlocked_collection_is_safe_to_hand_to_keyring(monkeypatch):
    import secretstorage

    class Conn:
        def close(self):
            pass

    class Coll:
        def is_locked(self):
            return False

    monkeypatch.setattr(secretstorage, "dbus_init", lambda: Conn())
    monkeypatch.setattr(secretstorage, "check_service_availability", lambda _c: True)
    monkeypatch.setattr(
        secretstorage, "get_collection_by_alias", lambda _c, _a: Coll()
    )

    assert session._collection_prompt_risk() is None


def test_a_missing_bus_does_not_become_a_locked_keyring(monkeypatch):
    """No session bus is the existing NoKeyringError path. keyring raises it at once."""
    import secretstorage
    from secretstorage.exceptions import SecretServiceNotAvailableException

    called = {"n": 0}

    def get_password(service, username):
        called["n"] += 1
        raise keyring.errors.NoKeyringError("no backend")

    def dbus_init():
        raise SecretServiceNotAvailableException("unset")

    monkeypatch.setattr(session, "adopt_session_bus", lambda: [])
    monkeypatch.setattr(session, "_backend_uses_secret_service", lambda _kr: True)
    monkeypatch.setattr(keyring, "get_keyring", lambda: object())
    monkeypatch.setattr(secretstorage, "dbus_init", dbus_init)
    monkeypatch.setattr(keyring, "get_password", get_password)

    assert session._keyring_get("config.pepper") is session._UNAVAILABLE
    assert called["n"] == 1
    text = session.keyring_problem()
    assert text.startswith("✗ no OS keyring reachable — ")
    assert "default collection" not in text


def test_a_locked_collection_never_calls_get_password(monkeypatch):
    monkeypatch.setattr(session, "adopt_session_bus", lambda: [])
    monkeypatch.setattr(
        session,
        "_prompt_risk",
        lambda: keyring.errors.KeyringLocked("the default collection is locked"),
    )

    def hang(service, username, password=None):
        raise AssertionError("get_password would open the unlock prompt")

    monkeypatch.setattr(keyring, "get_password", hang)
    started = time.monotonic()
    session.load_config()
    assert time.monotonic() - started < 2
    assert session.keyring_problem().startswith("✗ the OS keyring is locked — ")


def test_update_and_clear_do_not_prompt_when_the_collection_is_locked(monkeypatch, tmp_path):
    marker = tmp_path / "session.json"
    marker.write_text("{}")
    monkeypatch.setattr(session, "SESSION_FILE", marker)
    monkeypatch.setattr(
        session,
        "_prompt_risk",
        lambda: keyring.errors.KeyringLocked("the default collection is locked"),
    )
    def refuse_set(*_a, **_k):
        raise AssertionError("set would prompt")

    def refuse_delete(*_a, **_k):
        raise AssertionError("delete would prompt")

    monkeypatch.setattr(keyring, "set_password", refuse_set)
    monkeypatch.setattr(keyring, "delete_password", refuse_delete)

    with pytest.raises(RuntimeError, match="✗ the OS keyring is locked — "):
        session.update_tokens("access", "refresh")
    session.clear_session()
    assert marker.exists()


def test_unlocking_is_visible_on_the_next_probe(monkeypatch):
    monkeypatch.setattr(session, "_prompt_risk", lambda: None)
    state = {"locked": True}

    def get_password(service, username):
        if state["locked"]:
            raise keyring.errors.KeyringLocked("locked")
        return "pepper-from-keyring"

    monkeypatch.setattr(keyring, "get_password", get_password)
    first = session.probe_keyring()
    assert first is not None and first.startswith("✗ the OS keyring is locked — ")
    state["locked"] = False
    assert session.probe_keyring() is None
    assert session.keyring_problem() is None


def test_the_tool_handler_does_not_block_the_loop_on_the_probe(monkeypatch):
    def slow():
        time.sleep(0.3)
        return "✗ the OS keyring is locked — worker"

    monkeypatch.setattr(session, "probe_keyring", slow)
    ticks = {"n": 0}

    async def tick():
        while True:
            ticks["n"] += 1
            await asyncio.sleep(0.05)

    async def run():
        task = asyncio.create_task(tick())
        await asyncio.sleep(0)
        try:
            return await handle_call_tool("list_vault_sites", {})
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    result = asyncio.run(run())
    assert ticks["n"] >= 2, "the probe ran on the event loop and froze it"
    payload = json.loads(result[0].text)
    assert payload["error"].startswith("✗ the OS keyring is locked — ")


def test_child_environment_disables_autolaunch_and_drops_the_pepper(monkeypatch):
    """Unset bus address plus DISPLAY is what makes libdbus run dbus-launch.
    The pepper is in this process's environment because load_config puts it there."""
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1/bus")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("PSAMVAULT_PEPPER", "pepper-must-not-leak")
    monkeypatch.setenv("PSAMVAULT_VEK", "vek-must-not-leak")
    monkeypatch.setenv("PSAMVAULT_TOKEN", "token-must-not-leak")
    monkeypatch.setenv("PSAMVAULT_API_URL", "https://api.example.com")

    env = session.child_environment(None)

    assert env["DBUS_SESSION_BUS_ADDRESS"] == "disabled:"
    assert env["DISPLAY"] == ":0"
    assert "PSAMVAULT_PEPPER" not in env
    assert "PSAMVAULT_VEK" not in env
    assert "PSAMVAULT_TOKEN" not in env
    assert env["PSAMVAULT_API_URL"] == "https://api.example.com"
    assert "pepper-must-not-leak" not in env.values()

    passed = session.child_environment({
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/explicit-bus",
        "PSAMVAULT_PEPPER": "pepper-explicitly-passed",
    })
    assert passed["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/tmp/explicit-bus"
    assert passed["PSAMVAULT_PEPPER"] == "pepper-explicitly-passed"
    assert "PSAMVAULT_VEK" not in passed


def test_a_chainer_containing_secret_service_is_recognised(monkeypatch):
    """The negative case is a keyring this test builds. The machine's backend
    is irrelevant: a desktop Secret Service used to fail the assertion."""
    from keyring.backends.SecretService import Keyring as SecretServiceKeyring
    from keyring.backends.null import Keyring as NullKeyring

    class Chain:
        backends = [SecretServiceKeyring()]

    null = NullKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: null)

    assert session._backend_uses_secret_service(SecretServiceKeyring()) is True
    assert session._backend_uses_secret_service(Chain()) is True
    assert session._backend_uses_secret_service(null) is False
    assert session._backend_uses_secret_service(keyring.get_keyring()) is False


def test_adopted_bus_is_not_visible_to_a_child_command(monkeypatch, tmp_path):
    sock = _bind_socket(tmp_path / "bus")
    script = tmp_path / "show_env.py"
    script.write_text(
        "import os\n"
        "names = (\n"
        "    'DBUS_SESSION_BUS_ADDRESS',\n"
        "    'XDG_RUNTIME_DIR',\n"
        "    'GNOME_KEYRING_CONTROL',\n"
        "    'DBUS_STARTER_ADDRESS',\n"
        "    'HOME',\n"
        "    'DISPLAY',\n"
        "    'PSAMVAULT_CHILD_MARKER',\n"
        "    'PSAMVAULT_PEPPER',\n"
        "    'PSAMVAULT_VEK',\n"
        "    'PSAMVAULT_TOKEN',\n"
        "    'PSAMVAULT_API_URL',\n"
        ")\n"
        "for name in names:\n"
        "    print(name + '=' + os.environ.get(name, ''))\n",
        encoding="utf-8",
    )
    try:
        monkeypatch.setattr(session, "runtime_dir_candidates", lambda: [tmp_path])
        monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        monkeypatch.setenv("GNOME_KEYRING_CONTROL", str(tmp_path / "keyring"))
        monkeypatch.setenv("DBUS_STARTER_ADDRESS", f"unix:path={tmp_path / 'bus'}")
        monkeypatch.setenv("DISPLAY", ":99")
        monkeypatch.setenv("PSAMVAULT_CHILD_MARKER", "kept")
        monkeypatch.setenv("PSAMVAULT_PEPPER", "pepper-must-not-leak")
        monkeypatch.setenv("PSAMVAULT_VEK", "vek-must-not-leak")
        monkeypatch.setenv("PSAMVAULT_TOKEN", "token-must-not-leak")
        monkeypatch.setenv("PSAMVAULT_API_URL", "https://api.example.com")
        adopted = session.adopt_session_bus()
        assert "DBUS_SESSION_BUS_ADDRESS" in adopted
        assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={tmp_path / 'bus'}"
        result = asyncio.run(
            run_command_with_credential(
                command=f'"{sys.executable}" "{script}"',
                credential_value="not-printed",
                inject_as="env",
                env_var_name="TWINE_PASSWORD",
                timeout=30,
            )
        )
    finally:
        sock.close()

    assert result["exit_code"] == 0, result
    out = result["stdout"]
    # Unset would let libdbus autolaunch via DISPLAY. disabled: closes that.
    assert "DBUS_SESSION_BUS_ADDRESS=disabled:" in out
    assert "DISPLAY=:99" in out
    assert "XDG_RUNTIME_DIR=\n" in out
    assert "GNOME_KEYRING_CONTROL=\n" in out
    assert "DBUS_STARTER_ADDRESS=\n" in out
    assert "PSAMVAULT_PEPPER=\n" in out
    assert "PSAMVAULT_VEK=\n" in out
    assert "PSAMVAULT_TOKEN=\n" in out
    assert "PSAMVAULT_API_URL=https://api.example.com" in out
    assert f"unix:path={tmp_path / 'bus'}" not in out
    assert "pepper-must-not-leak" not in out
    assert "vek-must-not-leak" not in out
    assert "token-must-not-leak" not in out
    assert "PSAMVAULT_CHILD_MARKER=kept" in out
    assert "HOME=" in out and out.split("HOME=", 1)[1].splitlines()[0].strip()
    assert os.environ["PSAMVAULT_PEPPER"] == "pepper-must-not-leak"


def test_extra_env_can_pass_the_bus_through(monkeypatch, tmp_path):
    script = tmp_path / "show_env.py"
    script.write_text(
        "import os\n"
        "print('BUS=' + os.environ.get('DBUS_SESSION_BUS_ADDRESS', ''))\n"
        "print('XDG=' + os.environ.get('XDG_RUNTIME_DIR', ''))\n"
        "print('PEPPER=' + os.environ.get('PSAMVAULT_PEPPER', ''))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1/bus")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1")
    monkeypatch.setenv("PSAMVAULT_PEPPER", "pepper-must-not-leak")
    result = asyncio.run(
        run_command_with_credential(
            command=f'"{sys.executable}" "{script}"',
            credential_value="not-printed",
            inject_as="env",
            env_var_name="TWINE_PASSWORD",
            extra_env={
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/explicit-bus",
                "PSAMVAULT_PEPPER": "pepper-explicitly-passed",
            },
            timeout=30,
        )
    )
    assert result["exit_code"] == 0, result
    assert "BUS=unix:path=/tmp/explicit-bus" in result["stdout"]
    assert "PEPPER=pepper-explicitly-passed" in result["stdout"]
    assert "XDG=\n" in result["stdout"]
    assert "/run/user/1" not in result["stdout"]
    assert "pepper-must-not-leak" not in result["stdout"]
