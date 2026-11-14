"""generate_and_store_password: fakes and a mocked API, never a real vault.

The generated password is recovered by decrypting the ciphertext the client
posted. The assertions are that this value is absent from the tool result
and from the audit row.
"""

from __future__ import annotations

import json
import string

import httpx
import pytest
from pytest_httpx import HTTPXMock

from conftest import TEST_VEK
from mcp_server import api_client, tools
from mcp_server.crypto import decrypt_credentials
from mcp_server.main import TOOL_DEFINITIONS, handle_call_tool
from mcp_server.password_gen import SAFE_SYMBOLS
from mcp_server import mcp_use

pytestmark = pytest.mark.asyncio

_SITE = "example.com"


def _policy_dir():
    from pathlib import Path

    path = Path.home() / ".psamvault"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _clear_audit():
    for name in ("policy.json", "audit.jsonl", "audit.jsonl.1"):
        target = _policy_dir() / name
        if target.exists():
            target.unlink()


def _rows() -> list[dict]:
    path = _policy_dir() / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _audit_text() -> str:
    path = _policy_dir() / "audit.jsonl"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


@pytest.fixture
def clean_audit():
    _clear_audit()
    yield
    _clear_audit()


def _absent(secret: str, *blobs: str) -> None:
    assert secret
    for blob in blobs:
        assert secret not in blob
        assert secret[::-1] not in blob


def _mock_absent(httpx_mock: HTTPXMock, *, status: int = 200, body: dict | None = None):
    httpx_mock.add_response(
        method="GET",
        url=f"{api_client.BASE_URL}/vault/proxy/check/{_SITE}",
        json={"exists": False, "site_name": _SITE, "username_hint": None},
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{api_client.BASE_URL}/vault",
        status_code=status,
        json=body if body is not None else {"site_name": _SITE},
    )


def _posted_password(httpx_mock: HTTPXMock) -> tuple[str, dict]:
    posted = [request for request in httpx_mock.get_requests() if request.method == "POST"]
    assert len(posted) == 1
    body = json.loads(posted[0].content)
    creds = decrypt_credentials(TEST_VEK, body["encrypted_blob"], body["iv"])
    return creds["password"], body


async def test_success_returns_no_password_and_writes_an_audit_row(
    mock_tool_deps, httpx_mock: HTTPXMock, clean_audit
):
    _mock_absent(httpx_mock)
    result = await tools.generate_and_store_password(
        _SITE, username="ada", notes="desk drawer"
    )
    password, body = _posted_password(httpx_mock)
    rendered = json.dumps(result)
    assert set(result) == {"site", "length", "classes", "created"}
    assert result == {
        "site": _SITE,
        "length": 20,
        "classes": {"upper": True, "lower": True, "digits": True, "symbols": True},
        "created": True,
    }
    _absent(password, rendered, _audit_text(), json.dumps(body))
    creds = decrypt_credentials(TEST_VEK, body["encrypted_blob"], body["iv"])
    assert creds["username"] == "ada"
    assert creds["notes"] == "desk drawer"
    assert creds["password"] == password
    assert body["username_hint"] == "ada"
    rows = _rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["tool"] == "generate_and_store_password"
    assert row["entry"] == _SITE
    assert row["decision"] == "allow"
    assert row["ask"] is False
    assert "password" not in row
    assert password not in json.dumps(row)
    methods = [request.method for request in httpx_mock.get_requests()]
    assert methods.count("POST") == 1
    assert "PUT" not in methods


async def test_existing_entry_is_refused_and_nothing_is_posted(
    mock_tool_deps, httpx_mock: HTTPXMock, clean_audit
):
    httpx_mock.add_response(
        method="GET",
        url=f"{api_client.BASE_URL}/vault/proxy/check/{_SITE}",
        json={"exists": True, "site_name": _SITE, "username_hint": "ada"},
    )
    result = await tools.generate_and_store_password(_SITE)
    assert result["created"] is False
    assert "psamvault update" in result["error"]
    assert httpx_mock.get_requests()[0].method == "GET"
    assert all(request.method != "POST" for request in httpx_mock.get_requests())
    assert all(request.method != "PUT" for request in httpx_mock.get_requests())
    rendered = json.dumps(result)
    assert "password" not in result
    assert len(rendered) < 400


async def test_409_is_refused_without_a_second_write(
    mock_tool_deps, httpx_mock: HTTPXMock, clean_audit
):
    _mock_absent(httpx_mock, status=409, body={"detail": "already exists"})
    result = await tools.generate_and_store_password(_SITE)
    assert result["created"] is False
    assert result["error"] == (
        "Entry for 'example.com' already exists. Nothing was overwritten. "
        "Run `psamvault update` yourself to change it."
    )
    methods = [request.method for request in httpx_mock.get_requests()]
    assert methods == ["GET", "POST"]
    password, _body = _posted_password(httpx_mock)
    _absent(password, json.dumps(result), _audit_text())


async def test_not_gated_by_mcp_use_even_when_enforce_would_refuse(
    mock_tool_deps, httpx_mock: HTTPXMock, clean_audit, monkeypatch
):
    _policy_dir().joinpath("policy.json").write_text(
        json.dumps({
            "mcp_use": "enforce",
            "entries": {_SITE: {"allow_tools": ["use_credential"], "ask_each_time": True}},
        }),
        encoding="utf-8",
    )

    async def explode(tool: str, entry: str | None):
        raise AssertionError(f"mcp_use gate ran for {tool}")

    monkeypatch.setattr("mcp_server.mcp_gate.before_use", explode)
    _mock_absent(httpx_mock)
    result = await tools.generate_and_store_password(_SITE)
    assert result["created"] is True
    assert "STOP" not in json.dumps(result)
    row = _rows()[0]
    assert row["decision"] == "allow"
    assert row["reason"] == "not-listed"
    assert row["mcp_use"] == "enforce"
    assert row["ask"] is False
    assert "generate_and_store_password" in mcp_use.DISCOVERY
    assert "generate_and_store_password" not in mcp_use.TOOLS


async def test_off_mode_records_mcp_use_off(
    mock_tool_deps, httpx_mock: HTTPXMock, clean_audit
):
    _policy_dir().joinpath("policy.json").write_text(
        json.dumps({"mcp_use": "off"}), encoding="utf-8"
    )
    _mock_absent(httpx_mock)
    result = await tools.generate_and_store_password(_SITE)
    assert result["created"] is True
    assert _rows()[0]["reason"] == "mcp-use-off"
    assert _rows()[0]["decision"] == "allow"


async def test_invalid_site_names_are_refused_before_the_network(
    mock_tool_deps, httpx_mock: HTTPXMock, clean_audit
):
    for site in ("", "   ", "bad/name", "quote'name", 'quote"name', "has space", "a%b", None):
        result = await tools.generate_and_store_password(site)  # type: ignore[arg-type]
        assert "error" in result, site
        assert result.get("created") is not True
    assert httpx_mock.get_requests() == []
    assert _rows() == []


async def test_all_classes_off_is_refused(mock_tool_deps, httpx_mock: HTTPXMock, clean_audit):
    result = await tools.generate_and_store_password(
        _SITE, upper=False, lower=False, digits=False, symbols=False
    )
    assert "At least one character class" in result["error"]
    assert httpx_mock.get_requests() == []
    assert _rows() == []


async def test_length_bounds_are_refused_without_a_store(
    mock_tool_deps, httpx_mock: HTTPXMock, clean_audit
):
    too_short = await tools.generate_and_store_password(_SITE, length=3)
    too_long = await tools.generate_and_store_password(_SITE, length=129)
    not_int = await tools.generate_and_store_password(_SITE, length=True)  # type: ignore[arg-type]
    assert "at least 4" in too_short["error"]
    assert "at most 128" in too_long["error"]
    assert "integer" in not_int["error"]
    assert httpx_mock.get_requests() == []

    _mock_absent(httpx_mock)
    tight = await tools.generate_and_store_password(_SITE, length=4)
    assert tight["created"] is True
    assert tight["length"] == 4
    password, _body = _posted_password(httpx_mock)
    assert len(password) == 4
    assert any(ch in string.ascii_uppercase for ch in password)
    assert any(ch in string.ascii_lowercase for ch in password)
    assert any(ch in string.digits for ch in password)
    assert any(ch in SAFE_SYMBOLS for ch in password)
    _absent(password, json.dumps(tight), _audit_text())


async def test_default_symbols_in_the_stored_password_are_safe(
    mock_tool_deps, httpx_mock: HTTPXMock, clean_audit
):
    _mock_absent(httpx_mock)
    result = await tools.generate_and_store_password(
        _SITE, upper=False, lower=False, digits=False, symbols=True, length=48
    )
    assert result["created"] is True
    assert result["classes"]["symbols"] is True
    password, _body = _posted_password(httpx_mock)
    assert set(password) <= set(SAFE_SYMBOLS)
    assert not (set(password) & set("\"'\\`$% "))
    _absent(password, json.dumps(result))


async def test_dispatch_forwards_every_declared_argument(mock_tool_deps, monkeypatch):
    seen: dict = {}

    async def spy(**kwargs):
        seen.update(kwargs)
        return {"site": kwargs["site_name"], "length": 20, "classes": {}, "created": True}

    monkeypatch.setattr(tools, "generate_and_store_password", spy)
    declared = set()
    for tool in TOOL_DEFINITIONS:
        if tool.name == "generate_and_store_password":
            declared = set(tool.inputSchema["properties"])
    assert declared == {
        "site_name", "username", "notes", "length", "upper", "lower", "digits", "symbols"
    }
    payload = {
        "site_name": _SITE,
        "username": "ada",
        "notes": "n",
        "length": 12,
        "upper": False,
        "lower": True,
        "digits": False,
        "symbols": True,
    }
    blocks = await handle_call_tool("generate_and_store_password", payload)
    body = json.loads(blocks[0].text)
    assert body["created"] is True
    assert seen == payload


async def test_an_audit_write_failure_does_not_fail_or_leak(
    mock_tool_deps, httpx_mock: HTTPXMock, clean_audit, monkeypatch
):
    def explode(**kwargs):
        raise OSError("read-only")

    monkeypatch.setattr("mcp_server.audit.record_mcp_use", explode)
    _mock_absent(httpx_mock)
    result = await tools.generate_and_store_password(_SITE)
    password, _body = _posted_password(httpx_mock)
    assert result["created"] is True
    _absent(password, json.dumps(result))
