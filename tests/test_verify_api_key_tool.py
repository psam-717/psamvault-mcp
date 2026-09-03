"""Tests for the standalone verify_api_key tool.

Analysis:
- Unit under test: tools.verify_api_key(key_name, verify_url?) + registration
  in main.TOOL_DEFINITIONS.
- Behaviour: decrypt the named vault API key, resolve its provider from the
  vault entry's service hint (or an explicit verify_url override), probe the
  provider's read-only endpoint, and return pass/fail with status.
- Happy paths: known provider recipe + 200 -> verified; explicit verify_url
  override for unknown providers -> verified.
- Failure cases: 401/403 -> failed (key_invalid); 5xx/network ->
  failed (probe_unavailable); unknown provider with no verify_url -> error
  asking for verify_url; unknown key -> lookup error.
- Security contract: the key value never appears in any result.
- Mocked: vault lookup (api_client.get_api_key_entry) + HTTP (pytest_httpx).
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


def _encrypt_api_key(service: str, key: str) -> tuple[str, str]:
    iv = _os.urandom(12)
    payload = json.dumps({"service": service, "api_key": key, "notes": None}).encode("utf-8")
    aesgcm = AESGCM(TEST_VEK)
    ciphertext = aesgcm.encrypt(iv, payload, None)
    return ciphertext.hex(), iv.hex()


async def _fake_lookup(monkeypatch, key: str, service: str = "render"):
    blob, iv = _encrypt_api_key(service, key)

    async def fake_get_api_key_entry(access_token: str, key_name: str):
        return {"service": service, "encrypted_blob": blob, "iv": iv}

    monkeypatch.setattr(api_client, "get_api_key_entry", fake_get_api_key_entry)


async def _fake_lookup_real_shape(monkeypatch, key: str, service: str = "render"):
    """Mirror the REAL backend entry shape: no top-level 'service' key.

    The real vault entry carries service only inside the decrypted payload
    (plus a top-level service_hint). Regression guard for provider resolution.
    """
    blob, iv = _encrypt_api_key(service, key)

    async def fake_get_api_key_entry(access_token: str, key_name: str):
        return {
            "id": "k_123",
            "name": key_name,
            "service_hint": None,
            "encrypted_blob": blob,
            "iv": iv,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "notes": None,
        }

    monkeypatch.setattr(api_client, "get_api_key_entry", fake_get_api_key_entry)


class TestVerifyApiKeyTool:
    @pytest.mark.asyncio
    async def test_known_provider_recipe_200__returns_verified(
        self, mock_tool_deps, monkeypatch, httpx_mock: HTTPXMock
    ):
        await _fake_lookup(monkeypatch, "rnd_good_key_a1", service="render")
        httpx_mock.add_response(url=RENDER_VERIFY_URL, status_code=200)

        result = await tools.verify_api_key(key_name="hermes_atlas_render")

        assert result.get("success") is True
        assert result.get("verification") == "verified"
        assert result.get("status") == 200
        assert result.get("provider") == "render"
        assert "rnd_good_key_a1" not in json.dumps(result)

    @pytest.mark.asyncio
    async def test_401__returns_failed_key_invalid(
        self, mock_tool_deps, monkeypatch, httpx_mock: HTTPXMock
    ):
        await _fake_lookup(monkeypatch, "rnd_bad_key_b2", service="render")
        httpx_mock.add_response(url=RENDER_VERIFY_URL, status_code=401)

        result = await tools.verify_api_key(key_name="hermes_atlas_render")

        assert result.get("success") is False
        assert result.get("verification") == "failed"
        assert "key_invalid" in result.get("detail", "")
        assert "rnd_bad_key_b2" not in json.dumps(result)

    @pytest.mark.asyncio
    async def test_502__returns_failed_probe_unavailable(
        self, mock_tool_deps, monkeypatch, httpx_mock: HTTPXMock
    ):
        await _fake_lookup(monkeypatch, "rnd_key_c3", service="render")
        httpx_mock.add_response(url=RENDER_VERIFY_URL, status_code=502)

        result = await tools.verify_api_key(key_name="hermes_atlas_render")

        assert result.get("success") is False
        assert result.get("verification") == "failed"
        assert "probe_unavailable" in result.get("detail", "")

    @pytest.mark.asyncio
    async def test_unknown_provider_without_verify_url__error(
        self, mock_tool_deps, monkeypatch
    ):
        await _fake_lookup(monkeypatch, "rnd_acme_key_d4", service="acme")

        result = await tools.verify_api_key(key_name="acme_key")

        assert result.get("success") is not True
        assert "verify_url" in result.get("detail", result.get("error", ""))

    @pytest.mark.asyncio
    async def test_unknown_provider_with_verify_url_200__verified(
        self, mock_tool_deps, monkeypatch, httpx_mock: HTTPXMock
    ):
        await _fake_lookup(monkeypatch, "rnd_acme_key_e5", service="acme")
        httpx_mock.add_response(url="https://api.acme.dev/v1/whoami", status_code=200)

        result = await tools.verify_api_key(
            key_name="acme_key", verify_url="https://api.acme.dev/v1/whoami"
        )

        assert result.get("success") is True
        assert result.get("verification") == "verified"
        assert result.get("provider") == "acme"

    @pytest.mark.asyncio
    async def test_real_backend_shape_no_top_level_service__resolves_provider_from_decrypted(
        self, mock_tool_deps, monkeypatch, httpx_mock: HTTPXMock
    ):
        await _fake_lookup_real_shape(monkeypatch, "rnd_real_shape_f6", service="render")
        httpx_mock.add_response(url=RENDER_VERIFY_URL, status_code=200)

        result = await tools.verify_api_key(key_name="hermes_atlas_render")

        assert result.get("success") is True
        assert result.get("verification") == "verified"
        assert result.get("provider") == "render"
        assert "rnd_real_shape_f6" not in json.dumps(result)

    @pytest.mark.asyncio
    async def test_unknown_key__lookup_error(self, mock_tool_deps, monkeypatch):
        async def fake_missing(access_token: str, key_name: str):
            raise LookupError(f"no such key: {key_name}")

        monkeypatch.setattr(api_client, "get_api_key_entry", fake_missing)

        result = await tools.verify_api_key(key_name="does_not_exist")

        assert result.get("success") is not True
        assert "does_not_exist" in result.get("detail", result.get("error", ""))


class TestRegistration:
    def test_verify_api_key_is_registered_tool(self):
        from mcp_server import main

        names = [t.name for t in main.TOOL_DEFINITIONS]
        assert "verify_api_key" in names
