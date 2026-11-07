"""Regressions from the review of the mcp_use gate.

A policy file that is not UTF-8 is the same as a missing file: audit, with the
absent-key notice, and the audit row agrees. A locked keyring after an allow
returns the keyring message. A refusal does not read the keyring. A directory
chmod that fails still appends the row.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import keyring
import pytest

from mcp_server import audit, mcp_use, policy, tools
from mcp_server import session as vault_session
from mcp_server.main import _version_payload, handle_call_tool

_GATED_CALLS = (
    ("use_credential", {"site_name": "ledger-key", "target_url": "https://api.ledger.example/v1"}),
    ("run_with_credential", {"site_name": "ledger-key", "command": "true", "inject_as": "stdin"}),
    ("browser_login", {"site_name": "github.com"}),
    ("export_key_to_mcp_config", {"key_name": "ledger-key", "server_name": "ledger", "url": "https://api.ledger.example"}),
    ("export_key_to_env_file", {"key_name": "ledger-key", "env_var_name": "LEDGER_TOKEN"}),
    ("verify_api_key", {"key_name": "ledger-key"}),
)


def _vault() -> Path:
    path = Path.home() / ".psamvault"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _clear_policy_files() -> None:
    for name in ("policy.json", "audit.jsonl", "audit.jsonl.1"):
        target = _vault() / name
        if target.exists():
            target.unlink()


@pytest.fixture
def clean_policy():
    _clear_policy_files()
    yield
    _clear_policy_files()


def _watch_vek(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    def boom():
        seen.append("vek")
        raise AssertionError("get_vek was called")

    monkeypatch.setattr(tools, "get_vek", boom)
    return seen


def _watch_keyring(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    def get_password(service: str, key: str):
        seen.append(f"get_password:{key}")
        return None

    def get_keyring():
        seen.append("get_keyring")
        return keyring.get_keyring()

    monkeypatch.setattr(keyring, "get_password", get_password)
    monkeypatch.setattr(keyring, "get_keyring", get_keyring)
    return seen


@pytest.mark.asyncio
async def test_utf16_policy_is_audit_and_agrees_with_get_version(clean_policy, monkeypatch):
    target = _vault() / "policy.json"
    target.write_bytes('{"mcp_use": "audit"}\n'.encode("utf-16"))
    loaded = policy.load()
    assert loaded.mcp_use == "audit"
    assert loaded.mcp_use_implicit is True
    assert loaded.warnings
    status = _version_payload()["mcp_use"]
    assert status["mode"] == "audit"
    assert status["notice"] == mcp_use.ABSENT_DEFAULT_NOTICE
    assert status["warnings"]
    seen = _watch_vek(monkeypatch)
    result = await tools.use_credential("ledger-key", "https://api.ledger.example/v1")
    assert result.get("mcp_use") != "enforce"
    assert result.get("reason") != "not-listed"
    assert seen == []
    rows = audit.read()
    assert len(rows) == 1
    assert (rows[0]["decision"], rows[0]["reason"], rows[0]["mcp_use"], rows[0]["ask"]) == (
        "allow",
        "not-listed",
        "audit",
        False,
    )


@pytest.mark.asyncio
async def test_valid_utf8_enforce_still_denies_before_the_vek(clean_policy, monkeypatch):
    target = _vault() / "policy.json"
    target.write_text('{"mcp_use": "enforce"}\n', encoding="utf-8")
    if os.name == "posix":
        os.chmod(target, 0o600)
    status = _version_payload()["mcp_use"]
    assert status["mode"] == "enforce"
    assert status["notice"] is None
    seen = _watch_vek(monkeypatch)
    keyring_calls = _watch_keyring(monkeypatch)
    result = await tools.use_credential("ledger-key", "https://api.ledger.example/v1")
    assert result["reason"] == "not-listed"
    assert result["mcp_use"] == "enforce"
    assert result["error"].startswith("STOP.")
    assert seen == []
    assert keyring_calls == []
    row = audit.read()[0]
    assert (row["decision"], row["reason"], row["mcp_use"]) == ("deny", "not-listed", "enforce")


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "arguments"), _GATED_CALLS)
async def test_locked_keyring_after_allow_returns_the_keyring_message(
    clean_policy, monkeypatch, tmp_path, tool, arguments
):
    marker = tmp_path / "session.json"
    marker.write_text("{}")
    monkeypatch.setattr(vault_session, "SESSION_FILE", marker)
    monkeypatch.setattr(
        vault_session,
        "_prompt_risk",
        lambda: keyring.errors.KeyringLocked("the default collection is locked"),
    )
    seen = _watch_vek(monkeypatch)
    direct = await getattr(tools, tool)(**arguments)
    assert direct["error"].startswith("✗")
    assert "locked" in direct["error"].lower()
    assert "Not logged in" not in direct["error"]
    dispatched = json.loads((await handle_call_tool(tool, arguments))[0].text)
    assert dispatched["error"].startswith("✗")
    assert "locked" in dispatched["error"].lower()
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "arguments"), _GATED_CALLS)
async def test_enforce_denial_does_not_read_the_keyring(clean_policy, monkeypatch, tool, arguments):
    (_vault() / "policy.json").write_text('{"mcp_use": "enforce"}\n', encoding="utf-8")
    seen = _watch_vek(monkeypatch)
    keyring_calls = _watch_keyring(monkeypatch)
    result = await getattr(tools, tool)(**arguments)
    assert result["reason"] == "not-listed"
    assert seen == []
    assert keyring_calls == []
    dispatched = json.loads((await handle_call_tool(tool, arguments))[0].text)
    assert dispatched["reason"] == "not-listed"
    assert keyring_calls == []


def test_directory_chmod_failure_still_appends_one_row(tmp_path, monkeypatch):
    target = tmp_path / "vault" / "audit.jsonl"
    target.parent.mkdir()
    real_chmod = os.chmod

    def chmod(path, mode):
        if mode == 0o700:
            raise OSError(1, "Operation not permitted", str(path))
        return real_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", chmod)
    audit.record_mcp_use(
        tool="use_credential",
        entry="ledger-key",
        decision="deny",
        reason="not-listed",
        mcp_use="enforce",
        path=target,
    )
    raw = target.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert raw.count("\n") == 1
    assert '", "' not in raw
    assert '": "' not in raw
    rows = audit.read(target)
    assert len(rows) == 1
    assert rows[0]["tool"] == "use_credential"
    assert rows[0]["decision"] == "deny"
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) & 0o077 == 0
