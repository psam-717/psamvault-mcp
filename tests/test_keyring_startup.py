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
from pathlib import Path

import keyring
import pytest

from mcp_server import session
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
