"""Tests for the export_key_to_mcp_config tool.

Security contract under test: the vault key value may be written into the
target config file, but must NEVER appear in the tool's return value.
"""

import json
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from conftest import TEST_VEK

from mcp_server import api_client, tools

SAMPLE_CONFIG = """# Hermes Agent configuration
model: deepseek-v4-flash

mcp_servers:
  gbrain:
    command: C:\\Users\\psam\\.bun\\bin\\gbrain.exe
    enabled: true
"""


def _encrypt_api_key(service: str, key: str) -> tuple[str, str]:
    """Encrypt an API-key-shaped payload (service/api_key/notes) with TEST_VEK."""
    iv = _os.urandom(12)
    payload = json.dumps(
        {"service": service, "api_key": key, "notes": None}
    ).encode("utf-8")
    aesgcm = AESGCM(TEST_VEK)
    ciphertext = aesgcm.encrypt(iv, payload, None)
    return ciphertext.hex(), iv.hex()


@pytest.mark.asyncio
async def test_http_bearer_add_writes_config_and_never_returns_key(
    tmp_path, mock_tool_deps, monkeypatch
):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")
    blob, iv = _encrypt_api_key("render", "rnd_super_secret_789")

    async def fake_get_api_key_entry(access_token: str, key_name: str):
        return {"service": "render", "encrypted_blob": blob, "iv": iv}

    monkeypatch.setattr(api_client, "get_api_key_entry", fake_get_api_key_entry)

    result = await tools.export_key_to_mcp_config(
        key_name="hermes_atlas_render",
        server_name="render",
        url="https://mcp.render.com/mcp",
        config_path=str(cfg),
    )

    assert result.get("success") is True
    assert result["action"] == "added"
    assert result["server_name"] == "render"
    # The config file legitimately contains the key...
    text = cfg.read_text(encoding="utf-8")
    assert "rnd_super_secret_789" in text
    assert "url: https://mcp.render.com/mcp" in text
    # ...but the tool's return value must not leak it.
    assert "rnd_super_secret_789" not in json.dumps(result)
    assert "Bearer" not in json.dumps(result)


async def _fake_lookup(monkeypatch, key: str, service: str = "render"):
    """Monkeypatch api_client.get_api_key_entry to return an encrypted key."""
    blob, iv = _encrypt_api_key(service, key)

    async def fake_get_api_key_entry(access_token: str, key_name: str):
        return {"service": service, "encrypted_blob": blob, "iv": iv}

    monkeypatch.setattr(api_client, "get_api_key_entry", fake_get_api_key_entry)
    return fake_get_api_key_entry


@pytest.mark.asyncio
async def test_custom_header_mode_writes_header_without_bearer_prefix(
    tmp_path, mock_tool_deps, monkeypatch
):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")
    await _fake_lookup(monkeypatch, "rnd_custom_header_key_111")

    result = await tools.export_key_to_mcp_config(
        key_name="k",
        server_name="render",
        url="https://mcp.render.com/mcp",
        inject_as="api_key_header",
        header_name="X-API-Key",
        config_path=str(cfg),
    )

    assert result.get("success") is True
    text = cfg.read_text(encoding="utf-8")
    assert "X-API-Key: rnd_custom_header_key_111" in text
    assert "rnd_custom_header_key_111" not in json.dumps(result)


@pytest.mark.asyncio
async def test_env_mode_writes_stdio_server_env_var(tmp_path, mock_tool_deps, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")
    await _fake_lookup(monkeypatch, "sk_stdio_env_key_222", service="openai")

    result = await tools.export_key_to_mcp_config(
        key_name="openai",
        server_name="openai",
        command="uvx",
        args=["some-mcp-server"],
        inject_as="env",
        env_var_name="OPENAI_API_KEY",
        config_path=str(cfg),
    )

    assert result.get("success") is True
    text = cfg.read_text(encoding="utf-8")
    assert "command: uvx" in text
    assert "OPENAI_API_KEY: sk_stdio_env_key_222" in text
    assert "sk_stdio_env_key_222" not in json.dumps(result)


@pytest.mark.asyncio
async def test_dry_run_returns_summary_and_writes_nothing(
    tmp_path, mock_tool_deps, monkeypatch
):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")
    await _fake_lookup(monkeypatch, "rnd_dry_run_key_333")
    before = cfg.read_text(encoding="utf-8")

    result = await tools.export_key_to_mcp_config(
        key_name="k",
        server_name="render",
        url="https://mcp.render.com/mcp",
        config_path=str(cfg),
        dry_run=True,
    )

    assert result.get("success") is True
    assert result["dry_run"] is True
    assert result["action"] == "added"
    assert cfg.read_text(encoding="utf-8") == before
    assert "rnd_dry_run_key_333" not in json.dumps(result)


@pytest.mark.asyncio
async def test_existing_entry_errors_unless_replace(tmp_path, mock_tool_deps, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")
    await _fake_lookup(monkeypatch, "rnd_first_key_444")
    first = await tools.export_key_to_mcp_config(
        key_name="k",
        server_name="render",
        url="https://mcp.render.com/mcp",
        config_path=str(cfg),
    )
    assert first.get("success") is True

    await _fake_lookup(monkeypatch, "rnd_second_key_555")
    again = await tools.export_key_to_mcp_config(
        key_name="k",
        server_name="render",
        url="https://mcp.render.com/mcp",
        config_path=str(cfg),
    )
    assert "error" in again
    assert "already exists" in again["error"]

    replaced = await tools.export_key_to_mcp_config(
        key_name="k",
        server_name="render",
        url="https://mcp.render.com/mcp",
        config_path=str(cfg),
        replace=True,
    )
    assert replaced.get("success") is True
    assert replaced["action"] == "replaced"
    assert replaced.get("backup_path") is not None
    # Old key out, new key in — and still no key in the return values.
    text = cfg.read_text(encoding="utf-8")
    assert "rnd_second_key_555" in text
    assert "rnd_second_key_555" not in json.dumps(replaced)


@pytest.mark.asyncio
async def test_unknown_key_returns_error(tmp_path, mock_tool_deps, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")

    async def fake_missing(access_token: str, key_name: str):
        raise LookupError(f"no such key: {key_name}")

    monkeypatch.setattr(api_client, "get_api_key_entry", fake_missing)

    result = await tools.export_key_to_mcp_config(
        key_name="does_not_exist",
        server_name="render",
        url="https://mcp.render.com/mcp",
        config_path=str(cfg),
    )

    assert "error" in result
    assert "does_not_exist" in result["error"]
    # Nothing written on failure.
    assert "render:" not in cfg.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_unsupported_agent_errors(tmp_path, mock_tool_deps, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")
    await _fake_lookup(monkeypatch, "rnd_key_666")

    result = await tools.export_key_to_mcp_config(
        key_name="k",
        server_name="render",
        agent="claude",
        url="https://mcp.render.com/mcp",
        config_path=str(cfg),
    )

    assert "error" in result
    assert "claude" in result["error"]


@pytest.mark.asyncio
async def test_both_transports_error(tmp_path, mock_tool_deps, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")
    await _fake_lookup(monkeypatch, "rnd_key_777")

    result = await tools.export_key_to_mcp_config(
        key_name="k",
        server_name="both",
        url="https://mcp.render.com/mcp",
        command="npx",
        config_path=str(cfg),
    )

    assert "error" in result
    assert "exactly one transport" in result["error"]


@pytest.mark.asyncio
async def test_invalid_inject_mode_errors(tmp_path, mock_tool_deps, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")
    await _fake_lookup(monkeypatch, "rnd_key_888")

    result = await tools.export_key_to_mcp_config(
        key_name="k",
        server_name="render",
        url="https://mcp.render.com/mcp",
        inject_as="magic",
        config_path=str(cfg),
    )

    assert "error" in result
    assert "magic" in result["error"]


@pytest.mark.asyncio
async def test_env_inject_requires_env_var_name(tmp_path, mock_tool_deps, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")
    await _fake_lookup(monkeypatch, "rnd_key_999")

    result = await tools.export_key_to_mcp_config(
        key_name="k",
        server_name="render",
        command="uvx",
        inject_as="env",
        config_path=str(cfg),
    )

    assert "error" in result
    assert "env_var_name" in result["error"]


@pytest.mark.asyncio
async def test_default_config_path_honors_hermes_home_env(
    tmp_path, mock_tool_deps, monkeypatch
):
    fake_home = tmp_path / "hermes_home"
    fake_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(fake_home))
    await _fake_lookup(monkeypatch, "rnd_default_path_key_000")
    # No config_path passed — the tool resolves HERMES_HOME/config.yaml.
    # (Create the file there first so the adapter has something to edit.)
    target = fake_home / "config.yaml"
    target.write_text(SAMPLE_CONFIG, encoding="utf-8")

    result = await tools.export_key_to_mcp_config(
        key_name="k",
        server_name="render",
        url="https://mcp.render.com/mcp",
    )

    assert result.get("success") is True
    assert str(target) == result["config_path"]
    assert "render:" in target.read_text(encoding="utf-8")




