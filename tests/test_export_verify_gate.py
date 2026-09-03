"""Tests for the verification gate wired into export_key_to_mcp_config.

Analysis:
- Unit under test: export_key_to_mcp_config's verify-before-write gate.
- Behaviour (PLAN.md Decisions 1-4):
  * HTTP export for a provider WITH a bundled recipe -> auto-verify against
    the recipe probe; failure (key_invalid or probe_unavailable) hard-blocks
    the write and leaves the config untouched.
  * verify_url param overrides the recipe probe URL (hybrid recipe storage).
  * HTTP export for an unknown provider WITHOUT verify_url and WITHOUT
    skip_verify -> refused.
  * skip_verify=true is the loud escape hatch: export proceeds and the
    result records that verification was skipped.
  * Stdio/env exports cannot auto-verify in v1 -> they require skip_verify
    (explicit agent acknowledgment that a manual check was done).
  * Dry-run does NOT bypass verification (never export unverified, even as
    a preview).
- Mocked: api_client.get_api_key_entry (vault) + all HTTP via pytest_httpx.
- Security contract: the key never appears in any result dict.
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
SAMPLE_CONFIG = """# Hermes Agent configuration
model: deepseek-v4-flash

mcp_servers:
  gbrain:
    command: C:\\Users\\psam\\.bun\\bin\\gbrain.exe
    enabled: true
"""


def _encrypt_api_key(service: str, key: str) -> tuple[str, str]:
    iv = _os.urandom(12)
    payload = json.dumps({"service": service, "api_key": key, "notes": None}).encode("utf-8")
    aesgcm = AESGCM(TEST_VEK)
    ciphertext = aesgcm.encrypt(iv, payload, None)
    return ciphertext.hex(), iv.hex()


async def _fake_lookup(monkeypatch, key: str, service: str = "render"):
    """Monkeypatch api_client.get_api_key_entry to return an encrypted key."""
    blob, iv = _encrypt_api_key(service, key)

    async def fake_get_api_key_entry(access_token: str, key_name: str):
        return {"service": service, "encrypted_blob": blob, "iv": iv}

    monkeypatch.setattr(api_client, "get_api_key_entry", fake_get_api_key_entry)


def _write_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")
    return cfg


class TestAutoVerifyKnownProvider:
    @pytest.mark.asyncio
    async def test_recipe_provider_verify_200__export_succeeds(
        self, tmp_path, mock_tool_deps, monkeypatch, httpx_mock: HTTPXMock
    ):
        cfg = _write_config(tmp_path)
        await _fake_lookup(monkeypatch, "rnd_valid_key_123", service="render")
        httpx_mock.add_response(url=RENDER_VERIFY_URL, status_code=200)

        result = await tools.export_key_to_mcp_config(
            key_name="hermes_atlas_render",
            server_name="render",
            url="https://mcp.render.com/mcp",
            config_path=str(cfg),
        )

        assert result.get("success") is True
        assert result.get("verification") == "verified"
        # Probe actually hit the recipe endpoint with the bearer key.
        assert httpx_mock.get_requests()[0].url == RENDER_VERIFY_URL
        assert "rnd_valid_key_123" not in json.dumps(result)

    @pytest.mark.asyncio
    async def test_recipe_provider_verify_401__export_blocked_config_untouched(
        self, tmp_path, mock_tool_deps, monkeypatch, httpx_mock: HTTPXMock
    ):
        cfg = _write_config(tmp_path)
        await _fake_lookup(monkeypatch, "rnd_bad_key_456", service="render")
        before = cfg.read_text(encoding="utf-8")
        httpx_mock.add_response(url=RENDER_VERIFY_URL, status_code=401)

        result = await tools.export_key_to_mcp_config(
            key_name="hermes_atlas_render",
            server_name="render",
            url="https://mcp.render.com/mcp",
            config_path=str(cfg),
        )

        assert result.get("success") is not True
        assert "verification" in result and result["verification"] == "failed"
        assert "key_invalid" in result.get("detail", result.get("error", ""))
        assert cfg.read_text(encoding="utf-8") == before, "config must be untouched"
        assert "rnd_bad_key_456" not in json.dumps(result)

    @pytest.mark.asyncio
    async def test_recipe_provider_probe_502__export_blocked(
        self, tmp_path, mock_tool_deps, monkeypatch, httpx_mock: HTTPXMock
    ):
        cfg = _write_config(tmp_path)
        await _fake_lookup(monkeypatch, "rnd_key_789", service="render")
        before = cfg.read_text(encoding="utf-8")
        httpx_mock.add_response(url=RENDER_VERIFY_URL, status_code=502)

        result = await tools.export_key_to_mcp_config(
            key_name="hermes_atlas_render",
            server_name="render",
            url="https://mcp.render.com/mcp",
            config_path=str(cfg),
        )

        assert result.get("success") is not True
        assert "probe_unavailable" in result.get("detail", result.get("error", ""))
        assert cfg.read_text(encoding="utf-8") == before

    @pytest.mark.asyncio
    async def test_dry_run_still_verifies_and_blocks(
        self, tmp_path, mock_tool_deps, monkeypatch, httpx_mock: HTTPXMock
    ):
        cfg = _write_config(tmp_path)
        await _fake_lookup(monkeypatch, "rnd_dry_key_000", service="render")
        before = cfg.read_text(encoding="utf-8")
        httpx_mock.add_response(url=RENDER_VERIFY_URL, status_code=401)

        result = await tools.export_key_to_mcp_config(
            key_name="hermes_atlas_render",
            server_name="render",
            url="https://mcp.render.com/mcp",
            config_path=str(cfg),
            dry_run=True,
        )

        assert result.get("success") is not True
        assert cfg.read_text(encoding="utf-8") == before


class TestVerifyUrlOverrideAndUnknownProvider:
    @pytest.mark.asyncio
    async def test_verify_url_override__probes_custom_endpoint(
        self, tmp_path, mock_tool_deps, monkeypatch, httpx_mock: HTTPXMock
    ):
        cfg = _write_config(tmp_path)
        await _fake_lookup(monkeypatch, "rnd_acme_key_1", service="acme")
        httpx_mock.add_response(url="https://api.acme.dev/v1/whoami", status_code=200)

        result = await tools.export_key_to_mcp_config(
            key_name="acme_key",
            server_name="acme-mcp",
            url="https://mcp.acme.dev/mcp",
            verify_url="https://api.acme.dev/v1/whoami",
            config_path=str(cfg),
        )

        assert result.get("success") is True
        assert result.get("verification") == "verified"
        assert httpx_mock.get_requests()[0].url == "https://api.acme.dev/v1/whoami"

    @pytest.mark.asyncio
    async def test_unknown_provider_without_verify_url__refused(
        self, tmp_path, mock_tool_deps, monkeypatch
    ):
        cfg = _write_config(tmp_path)
        await _fake_lookup(monkeypatch, "rnd_unknown_key_2", service="acme")
        before = cfg.read_text(encoding="utf-8")

        result = await tools.export_key_to_mcp_config(
            key_name="acme_key",
            server_name="acme-mcp",
            url="https://mcp.acme.dev/mcp",
            config_path=str(cfg),
        )

        assert result.get("success") is not True
        assert "verify_url" in result.get("detail", result.get("error", ""))
        assert cfg.read_text(encoding="utf-8") == before

    @pytest.mark.asyncio
    async def test_skip_verify_true__exports_with_loud_record(
        self, tmp_path, mock_tool_deps, monkeypatch
    ):
        cfg = _write_config(tmp_path)
        await _fake_lookup(monkeypatch, "rnd_skip_key_3", service="acme")

        result = await tools.export_key_to_mcp_config(
            key_name="acme_key",
            server_name="acme-mcp",
            url="https://mcp.acme.dev/mcp",
            config_path=str(cfg),
            skip_verify=True,
        )

        assert result.get("success") is True
        assert result.get("verification") == "skipped"
        assert "rnd_skip_key_3" not in json.dumps(result)


class TestStdioEnvContract:
    @pytest.mark.asyncio
    async def test_stdio_env_export_without_skip_verify__refused(
        self, tmp_path, mock_tool_deps, monkeypatch
    ):
        cfg = _write_config(tmp_path)
        await _fake_lookup(monkeypatch, "sk_limited_token", service="openai")
        before = cfg.read_text(encoding="utf-8")

        result = await tools.export_key_to_mcp_config(
            key_name="openai",
            server_name="openai",
            command="uvx",
            args=["some-mcp-server"],
            inject_as="env",
            env_var_name="OPENAI_API_KEY",
            config_path=str(cfg),
        )

        assert result.get("success") is not True
        assert "skip_verify" in result.get("detail", result.get("error", ""))
        assert cfg.read_text(encoding="utf-8") == before

    @pytest.mark.asyncio
    async def test_stdio_env_export_with_skip_verify__succeeds(
        self, tmp_path, mock_tool_deps, monkeypatch
    ):
        cfg = _write_config(tmp_path)
        await _fake_lookup(monkeypatch, "sk_limited_token", service="openai")

        result = await tools.export_key_to_mcp_config(
            key_name="openai",
            server_name="openai",
            command="uvx",
            args=["some-mcp-server"],
            inject_as="env",
            env_var_name="OPENAI_API_KEY",
            config_path=str(cfg),
            skip_verify=True,
        )

        assert result.get("success") is True
        assert result.get("verification") == "skipped"
