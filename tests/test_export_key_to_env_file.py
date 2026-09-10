"""Tests for the export_key_to_env_file tool.

Security contract under test: the vault key value may be written into the target .env file, but
must NEVER appear in the tool's return value.
"""

import json
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pytest_httpx import HTTPXMock

from conftest import TEST_VEK

from mcp_server import api_client, tools

RENDER_VERIFY_URL = "https://api.render.com/v1/owners"
SECRET = "rnd_super_secret_789"


def _encrypt_api_key(service: str, key: str) -> tuple[str, str]:
    """Encrypt an API-key-shaped payload (service/api_key/notes) with TEST_VEK."""
    iv = _os.urandom(12)
    payload = json.dumps({"service": service, "api_key": key, "notes": None}).encode("utf-8")
    ciphertext = AESGCM(TEST_VEK).encrypt(iv, payload, None)
    return ciphertext.hex(), iv.hex()


def _patch_key(monkeypatch, service: str) -> None:
    blob, iv = _encrypt_api_key(service, SECRET)

    async def fake_get_api_key_entry(access_token: str, key_name: str):
        return {"service": service, "encrypted_blob": blob, "iv": iv}

    monkeypatch.setattr(api_client, "get_api_key_entry", fake_get_api_key_entry)


@pytest.mark.asyncio
async def test_writes_variable_and_never_returns_the_key(
    tmp_path, mock_tool_deps, monkeypatch, httpx_mock: HTTPXMock
):
    env_file = tmp_path / ".env"
    env_file.write_text("EXISTING=1\n", encoding="utf-8")
    httpx_mock.add_response(url=RENDER_VERIFY_URL, status_code=200)
    _patch_key(monkeypatch, "render")

    result = await tools.export_key_to_env_file(
        key_name="hermes_atlas_render",
        env_var_name="RENDER_API_KEY",
        env_path=str(env_file),
    )

    assert result.get("success") is True
    assert result["verification"] == "verified"
    assert result["action"] == "appended"
    assert result["variable"] == "RENDER_API_KEY"
    text = env_file.read_text(encoding="utf-8")
    assert "RENDER_API_KEY=" + SECRET in text
    assert "EXISTING=1" in text, "unrelated variables survive"
    assert SECRET not in json.dumps(result), "the tool must never return the key"


@pytest.mark.asyncio
async def test_skip_verify_allows_a_key_without_a_recipe(
    tmp_path, mock_tool_deps, monkeypatch
):
    env_file = tmp_path / ".env"
    _patch_key(monkeypatch, "tavily")

    result = await tools.export_key_to_env_file(
        key_name="tavily",
        env_var_name="TAVILY_API_KEY",
        env_path=str(env_file),
        skip_verify=True,
    )

    assert result.get("success") is True
    assert result["verification"] == "skipped"
    assert "TAVILY_API_KEY=" + SECRET in env_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_unverifiable_key_without_skip_verify_is_blocked(
    tmp_path, mock_tool_deps, monkeypatch
):
    env_file = tmp_path / ".env"
    _patch_key(monkeypatch, "tavily")

    result = await tools.export_key_to_env_file(
        key_name="tavily",
        env_var_name="TAVILY_API_KEY",
        env_path=str(env_file),
    )

    assert result["success"] is False
    assert result["verification"] == "failed"
    assert not env_file.exists(), "a blocked export must not write anything"


@pytest.mark.asyncio
async def test_failed_verification_blocks_the_write(
    tmp_path, mock_tool_deps, monkeypatch, httpx_mock: HTTPXMock
):
    env_file = tmp_path / ".env"
    env_file.write_text("KEEP=me\n", encoding="utf-8")
    httpx_mock.add_response(url=RENDER_VERIFY_URL, status_code=401)
    _patch_key(monkeypatch, "render")

    result = await tools.export_key_to_env_file(
        key_name="hermes_atlas_render",
        env_var_name="RENDER_API_KEY",
        env_path=str(env_file),
    )

    assert result["success"] is False
    assert result["verification"] == "failed"
    assert env_file.read_text(encoding="utf-8") == "KEEP=me\n"


@pytest.mark.asyncio
async def test_updates_in_place_on_a_second_run(tmp_path, mock_tool_deps, monkeypatch, httpx_mock: HTTPXMock):
    env_file = tmp_path / ".env"
    env_file.write_text("RENDER_API_KEY=stale\n", encoding="utf-8")
    httpx_mock.add_response(url=RENDER_VERIFY_URL, status_code=200)
    _patch_key(monkeypatch, "render")

    result = await tools.export_key_to_env_file(
        key_name="hermes_atlas_render",
        env_var_name="RENDER_API_KEY",
        env_path=str(env_file),
    )

    assert result["action"] == "updated"
    text = env_file.read_text(encoding="utf-8")
    assert text.count("RENDER_API_KEY=") == 1
    assert "stale" not in text


@pytest.mark.asyncio
async def test_dry_run_reports_without_writing(tmp_path, mock_tool_deps, monkeypatch):
    env_file = tmp_path / ".env"
    _patch_key(monkeypatch, "tavily")

    result = await tools.export_key_to_env_file(
        key_name="tavily",
        env_var_name="TAVILY_API_KEY",
        env_path=str(env_file),
        skip_verify=True,
        dry_run=True,
    )

    assert result["success"] is True
    assert result["action"] == "appended"
    assert result["backup_path"] is None
    assert not env_file.exists(), "dry_run must not create the file"


@pytest.mark.asyncio
async def test_unknown_agent_without_env_path_errors(tmp_path, mock_tool_deps, monkeypatch):
    _patch_key(monkeypatch, "tavily")

    result = await tools.export_key_to_env_file(
        key_name="tavily",
        env_var_name="TAVILY_API_KEY",
        agent="groku",
        skip_verify=True,
    )

    assert "error" in result
    assert "groku" in result["error"]
    assert "env_path" in result["error"]
