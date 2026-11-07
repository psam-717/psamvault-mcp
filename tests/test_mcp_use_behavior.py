"""MCP-use policy on the tools that decrypt a secret.

These tests describe the CLI contract (psamvault-cli ``policy.mcp_plan``,
``mcp_use.decide`` / ``should_ask`` / ``finalize``, and ``audit.record_mcp_use``)
as this server must behave. They call the public tools. On a build that still
decrypts with no policy check they fail because the VEK is read, nothing is
audited, and an unlisted tool is not refused.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from conftest import TEST_VEK
from mcp_server import tools
from mcp_server.main import handle_call_tool

pytestmark = pytest.mark.asyncio

GATED = (
    "use_credential",
    "run_with_credential",
    "browser_login",
    "export_key_to_mcp_config",
    "export_key_to_env_file",
    "verify_api_key",
)

NOTICE = (
    "mcp_use is not set, so it defaults to audit; "
    "a future release will default to enforce; "
    "run `psamvault policy mcp-use audit` to keep audit explicitly, "
    "or `enforce` to opt in now"
)


def _vault_dir() -> Path:
    path = Path.home() / ".psamvault"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_policy(body: dict | str) -> Path:
    target = _vault_dir() / "policy.json"
    text = body if isinstance(body, str) else json.dumps(body)
    target.write_text(text, encoding="utf-8")
    return target


def _clear_policy() -> None:
    for name in ("policy.json", "audit.jsonl", "audit.jsonl.1"):
        path = _vault_dir() / name
        if path.exists():
            path.unlink()


def _rows() -> list[dict]:
    path = _vault_dir() / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def clean_policy():
    _clear_policy()
    yield
    _clear_policy()


def _watch_vek(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record a decrypt or a browser launch, then stop.

    ``mock_tool_deps`` replaces ``_decrypt_site_credential`` with a stub that
    does not call ``get_vek``, and ``browser_login`` launches Chromium after
    that stub returns. Watching both keeps the test from opening a browser
    while still failing on main, where the tool proceeds past the missing gate.
    """
    seen: list[str] = []

    def boom_vek():
        seen.append("vek")
        raise AssertionError("get_vek was called")

    async def boom_decrypt(site_name: str):
        seen.append("decrypt")
        raise AssertionError("credential was decrypted")

    async def boom_browser():
        seen.append("browser")
        raise AssertionError("browser was launched")

    monkeypatch.setattr(tools, "get_vek", boom_vek)
    monkeypatch.setattr(tools, "_decrypt_site_credential", boom_decrypt)
    monkeypatch.setattr(tools, "_get_browser", boom_browser)
    return seen


async def _call(name: str, arguments: dict) -> dict:
    blocks = await handle_call_tool(name, arguments)
    return json.loads(blocks[0].text)


async def test_get_version_reports_the_absent_default_notice(clean_policy):
    payload = json.loads((await handle_call_tool("get_version", {}))[0].text)
    assert payload["mcp_use"]["mode"] == "audit"
    assert payload["mcp_use"]["notice"] == NOTICE
    assert _rows() == []


async def test_get_version_omits_the_notice_when_audit_is_explicit(clean_policy):
    _write_policy({"mcp_use": "audit"})
    payload = json.loads((await handle_call_tool("get_version", {}))[0].text)
    assert payload["mcp_use"]["mode"] == "audit"
    assert payload["mcp_use"]["notice"] is None


async def test_get_version_names_an_unknown_mode_without_the_flip_notice(clean_policy):
    _write_policy({"mcp_use": "enforced"})
    payload = json.loads((await handle_call_tool("get_version", {}))[0].text)
    assert payload["mcp_use"]["mode"] == "audit"
    assert payload["mcp_use"]["notice"] is None
    assert any("enforced" in warning for warning in payload["mcp_use"]["warnings"])


async def test_a_broken_policy_file_is_audit_and_does_not_refuse(mock_tool_deps, httpx_mock: HTTPXMock, clean_policy):
    """A typo must not turn into enforce. The file is not rewritten."""
    target = _write_policy("{not json")
    before = target.read_bytes()
    _mock_own_site(httpx_mock)
    result = await tools.use_credential(
        "leakcheck.example",
        "https://leakcheck.example/home",
        inject_as="bearer_token",
    )
    assert "error" not in result, result
    assert target.read_bytes() == before
    assert len(_rows()) == 1
    assert _rows()[0]["decision"] == "allow"
    assert _rows()[0]["reason"] == "not-listed"
    assert _rows()[0]["mcp_use"] == "audit"
    assert _rows()[0]["ask"] is False


async def test_enforce_refuses_an_unlisted_tool_before_the_vek(
    mock_tool_deps, monkeypatch, clean_policy
):
    _write_policy({"mcp_use": "enforce", "entries": {"ledger-key": {"allow_hosts": ["api.ledger.example"]}}})
    seen = _watch_vek(monkeypatch)
    result = await tools.use_credential("ledger-key", "https://api.ledger.example/v1")
    assert seen == []
    assert result["decision"] == "deny"
    assert result["reason"] == "not-listed"
    assert result["mcp_use"] == "enforce"
    assert result["error"].startswith("STOP.")
    assert "psamvault policy allow-tool ledger-key use_credential" in result["error"]
    assert "psamvault policy mcp-use off" not in result["error"]
    assert "SUPERSECRET" not in result["error"]
    rows = _rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["command"] == "mcp use_credential"
    assert row["entry"] == "ledger-key"
    assert row["caller"] == "mcp"
    assert row["decision"] == "deny"
    assert row["reason"] == "not-listed"
    assert row["tool"] == "use_credential"
    assert row["mcp_use"] == "enforce"
    assert row["ask"] is False
    assert row["policy"] is None
    assert row["token_id"] is None
    assert row["signals"] == []
    assert row["tty"] is False
    assert "host" not in row
    assert row["ts"].endswith("Z")
    assert row["pid"] == os.getpid()
    raw = (_vault_dir() / "audit.jsonl").read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert '", "' not in raw
    assert '": "' not in raw
    if os.name == "posix":
        assert (_vault_dir() / "audit.jsonl").stat().st_mode & 0o077 == 0


async def test_a_missing_session_still_records_the_refusal(monkeypatch, clean_policy):
    """The dispatcher must not skip the audit row when the vault session is absent."""
    _write_policy({"mcp_use": "enforce"})
    seen = _watch_vek(monkeypatch)
    result = await _call(
        "use_credential",
        {"site_name": "ledger-key", "target_url": "https://api.ledger.example/v1"},
    )
    assert seen == []
    assert result["reason"] == "not-listed"
    assert result["error"].startswith("STOP.")
    assert len(_rows()) == 1
    assert _rows()[0]["decision"] == "deny"


async def test_the_dispatcher_refuses_before_the_tool_reads_the_vek(
    mock_tool_deps, monkeypatch, clean_policy
):
    _write_policy({"mcp_use": "enforce"})
    seen = _watch_vek(monkeypatch)
    result = await _call(
        "browser_login",
        {"site_name": "github.com"},
    )
    assert seen == []
    assert result["reason"] == "not-listed"
    assert "psamvault policy allow-tool github.com browser_login" in result["error"]
    assert len(_rows()) == 1


@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [
        ("use_credential", {"site_name": "ledger-key", "target_url": "https://api.ledger.example/v1"}),
        ("run_with_credential", {"site_name": "ledger-key", "command": "true", "inject_as": "stdin"}),
        ("browser_login", {"site_name": "github.com"}),
        (
            "export_key_to_mcp_config",
            {"key_name": "ledger-key", "server_name": "ledger", "url": "https://api.ledger.example"},
        ),
        ("export_key_to_env_file", {"key_name": "ledger-key", "env_var_name": "LEDGER_TOKEN"}),
        ("verify_api_key", {"key_name": "ledger-key"}),
    ],
)
async def test_every_gated_tool_stops_before_the_vek(mock_tool_deps, monkeypatch, clean_policy, tool, kwargs):
    _write_policy({"mcp_use": "enforce"})
    seen = _watch_vek(monkeypatch)
    result = await getattr(tools, tool)(**kwargs)
    assert seen == [], tool
    assert result["reason"] == "not-listed"
    assert result["error"].startswith("STOP.")
    assert f"psamvault policy allow-tool" in result["error"]
    assert tool in result["error"]


async def test_audit_mode_allows_an_unlisted_tool_and_writes_one_row(
    mock_tool_deps, httpx_mock: HTTPXMock, clean_policy
):
    _mock_own_site(httpx_mock)
    result = await tools.use_credential(
        "leakcheck.example",
        "https://leakcheck.example/home",
        inject_as="bearer_token",
    )
    assert "error" not in result, result
    assert "mcp_use" not in result
    assert "notice" not in result
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["decision"] == "allow"
    assert rows[0]["reason"] == "not-listed"
    assert rows[0]["mcp_use"] == "audit"
    assert rows[0]["ask"] is False
    assert rows[0]["tool"] == "use_credential"


async def test_audit_does_not_ask_when_the_flag_is_true(
    mock_tool_deps, httpx_mock: HTTPXMock, clean_policy
):
    _write_policy(
        {
            "mcp_use": "audit",
            "entries": {"leakcheck.example": {"allow_tools": ["use_credential"], "ask_each_time": True}},
        }
    )
    _mock_own_site(httpx_mock)
    result = await tools.use_credential(
        "leakcheck.example",
        "https://leakcheck.example/home",
        inject_as="bearer_token",
    )
    assert "error" not in result, result
    row = _rows()[0]
    assert (row["decision"], row["reason"], row["ask"]) == ("allow", "listed", False)


async def test_off_allows_without_consulting_the_list(
    mock_tool_deps, httpx_mock: HTTPXMock, clean_policy
):
    _write_policy({"mcp_use": "off", "entries": {"other": {"allow_tools": ["browser_login"], "ask_each_time": True}}})
    _mock_own_site(httpx_mock)
    result = await tools.use_credential(
        "leakcheck.example",
        "https://leakcheck.example/home",
        inject_as="bearer_token",
    )
    assert "error" not in result, result
    row = _rows()[0]
    assert (row["decision"], row["reason"], row["ask"], row["mcp_use"]) == (
        "allow",
        "mcp-use-off",
        False,
        "off",
    )


async def test_enforce_allows_a_listed_tool_then_still_checks_the_host(
    mock_tool_deps, httpx_mock: HTTPXMock, clean_policy
):
    """The tool decision is allow. The destination check still refuses a foreign host."""
    _write_policy(
        {"mcp_use": "enforce", "entries": {"leakcheck.example": {"allow_tools": ["Use_Credential"]}}}
    )
    _mock_vault_only(httpx_mock)
    result = await tools.use_credential(
        "leakcheck.example",
        "https://httpbin.org/anything",
        inject_as="bearer_token",
    )
    assert result.get("reason") != "not-listed"
    assert "httpbin.org" in result["error"]
    assert "allow_hosts" in result["error"]
    rows = _rows()
    assert len(rows) == 1
    assert (rows[0]["decision"], rows[0]["reason"], rows[0]["ask"]) == ("allow", "listed", False)


async def test_a_leaf_name_unions_matching_entries(mock_tool_deps, clean_policy):
    _write_policy(
        {
            "mcp_use": "enforce",
            "entries": {
                "KEY": {"allow_tools": ["use_credential"], "ask_each_time": True},
                "project/.env/KEY": {"allow_tools": ["browser_login"]},
            },
        }
    )
    result = await tools.use_credential(
        "project/.env/KEY",
        "https://api.github.com/user",
        inject_as="bearer_token",
    )
    # ask_each_time is true on a match, and this process has no elicitation session,
    # so the allow becomes ask-unanswered before the VEK is used.
    assert result["reason"] == "ask-unanswered"
    assert result["error"].startswith("STOP.")
    assert _rows()[0]["ask"] is False
    assert _rows()[0]["decision"] == "deny"


async def test_enforce_ask_without_a_session_fails_closed_before_the_vek(
    mock_tool_deps, monkeypatch, clean_policy
):
    _write_policy(
        {
            "mcp_use": "enforce",
            "entries": {"ledger-key": {"allow_tools": ["use_credential"], "ask_each_time": True}},
        }
    )
    seen = _watch_vek(monkeypatch)
    result = await tools.use_credential("ledger-key", "https://api.ledger.example/v1")
    assert seen == []
    assert result["reason"] == "ask-unanswered"
    assert result["error"].startswith("STOP.")
    assert "psamvault policy allow-tool" not in result["error"]
    row = _rows()[0]
    assert (row["decision"], row["reason"], row["ask"]) == ("deny", "ask-unanswered", False)


async def test_a_yes_keeps_the_allow_and_a_no_denies(
    mock_tool_deps, httpx_mock: HTTPXMock, monkeypatch, clean_policy
):
    from mcp_server import mcp_gate

    _write_policy(
        {
            "mcp_use": "enforce",
            "entries": {"leakcheck.example": {"allow_tools": ["use_credential"], "ask_each_time": True}},
        }
    )
    answers = iter([True, False])

    async def answer(tool: str, entry: str) -> bool:
        assert tool == "use_credential"
        assert entry == "leakcheck.example"
        return next(answers)

    monkeypatch.setattr(mcp_gate, "elicit", answer)
    _mock_own_site(httpx_mock, copies=1)
    allowed = await tools.use_credential(
        "leakcheck.example",
        "https://leakcheck.example/home",
        inject_as="bearer_token",
    )
    assert "error" not in allowed, allowed
    denied = await tools.use_credential(
        "leakcheck.example",
        "https://leakcheck.example/home",
        inject_as="bearer_token",
    )
    assert denied["reason"] == "ask-declined"
    assert denied["error"].startswith("STOP.")
    assert "psamvault policy mcp-use off" not in denied["error"]
    rows = _rows()
    assert len(rows) == 2
    assert (rows[0]["decision"], rows[0]["reason"], rows[0]["ask"]) == ("allow", "listed", True)
    assert (rows[1]["decision"], rows[1]["reason"], rows[1]["ask"]) == ("deny", "ask-declined", True)


async def test_an_elicitation_error_is_ask_unanswered_and_does_not_read_the_vek(
    mock_tool_deps, monkeypatch, clean_policy
):
    from mcp_server import mcp_gate

    _write_policy(
        {
            "mcp_use": "enforce",
            "entries": {"ledger-key": {"allow_tools": ["use_credential"], "ask_each_time": True}},
        }
    )

    async def broken(tool: str, entry: str):
        raise TimeoutError("host timed out")

    monkeypatch.setattr(mcp_gate, "elicit", broken)
    seen = _watch_vek(monkeypatch)
    result = await tools.use_credential("ledger-key", "https://api.ledger.example/v1")
    assert seen == []
    assert result["reason"] == "ask-unanswered"
    assert _rows()[0]["reason"] == "ask-unanswered"


async def test_discovery_tools_are_not_gated_and_write_no_row(mock_tool_deps, clean_policy):
    _write_policy({"mcp_use": "enforce"})
    version = json.loads((await handle_call_tool("get_version", {}))[0].text)
    assert version["mcp_use"]["mode"] == "enforce"
    found = json.loads((await handle_call_tool("search_vault_tools", {"query": ""}))[0].text)
    assert "use_credential" in found
    username = await tools.get_username_for_site("github.com")
    assert username.get("username") == "testuser"
    assert username.get("reason") != "not-listed"
    scanned = await tools.scan_and_protect(project_dir=str(Path.home()))
    assert scanned.get("reason") != "not-listed"
    assert _rows() == []


async def test_an_audit_write_failure_does_not_fail_the_tool(
    mock_tool_deps, httpx_mock: HTTPXMock, monkeypatch, clean_policy
):
    from mcp_server import audit

    def explode(**kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(audit, "record_mcp_use", explode)
    _mock_own_site(httpx_mock)
    result = await tools.use_credential(
        "leakcheck.example",
        "https://leakcheck.example/home",
        inject_as="bearer_token",
    )
    assert "error" not in result, result


def _encrypt(payload: dict) -> tuple[str, str]:
    iv = os.urandom(12)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    ciphertext = AESGCM(TEST_VEK).encrypt(iv, json.dumps(payload).encode(), None)
    return ciphertext.hex(), iv.hex()


def _mock_vault_only(httpx_mock: HTTPXMock, copies: int = 1) -> None:
    from mcp_server import api_client

    blob, iv = _encrypt({"username": "leakuser", "password": "DUMMY-pw", "notes": ""})
    for _ in range(copies):
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/apikeys",
            json={"entries": []},
        )
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/vault/leakcheck.example",
            json={"encrypted_blob": blob, "iv": iv, "site_name": "leakcheck.example"},
        )


def _mock_own_site(httpx_mock: HTTPXMock, copies: int = 1) -> None:
    _mock_vault_only(httpx_mock, copies=copies)
    for _ in range(copies):
        httpx_mock.add_response(method="GET", url="https://leakcheck.example/home", json={"ok": True})

