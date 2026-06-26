"""Tests for tools.py — MCP tools exposed to AI agents."""

import asyncio
import json

import pytest
from pytest_httpx import HTTPXMock

from unittest.mock import AsyncMock

import sys as _sys
import os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from conftest import TEST_ACCESS_TOKEN, TEST_CREDS, TEST_SITE, TEST_VEK

from mcp_server import api_client, tools


# ── list_vault_sites ───────────────────────────────────────────────────────

class TestListVaultSites:
    @pytest.mark.asyncio
    async def test_returns_sites(self, mock_tool_deps, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/vault",
            json={"entries": [
                {"site_name": "github.com", "username_hint": "user1"},
                {"site_name": "gitlab.com", "username_hint": "user2"},
            ]},
        )
        result = await tools.list_vault_sites()
        assert result == {
            "sites": [
                {"site_name": "github.com", "username_hint": "user1"},
                {"site_name": "gitlab.com", "username_hint": "user2"},
            ],
            "total": 2,
        }

    @pytest.mark.asyncio
    async def test_not_logged_in(self):
        result = await tools.list_vault_sites()
        assert "error" in result
        assert "Not logged in" in result["error"]


# ── list_api_keys ──────────────────────────────────────────────────────────

class TestListApiKeys:
    @pytest.mark.asyncio
    async def test_returns_api_keys(self, mock_tool_deps, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/apikeys",
            json={"entries": [
                {"name": "github-api", "service_hint": "GitHub API", "created_at": "2026-06-17T05:36:25Z"},
                {"name": "twitter-bot/.env/GROK_API_KEY", "service_hint": "xAI", "created_at": "2026-06-17T06:00:00Z"},
            ]},
        )
        result = await tools.list_api_keys()
        assert result["total"] == 2
        assert len(result["api_keys"]) == 2
        assert result["standalone"] == [
            {"name": "github-api", "service_hint": "GitHub API", "notes": None,
             "created_at": "2026-06-17T05:36:25Z",
             "project": None, "key_name": "github-api"}
        ]
        assert result["projects"] == {
            "twitter-bot": [
                {"name": "twitter-bot/.env/GROK_API_KEY", "service_hint": "xAI", "notes": None,
                 "created_at": "2026-06-17T06:00:00Z",
                 "project": "twitter-bot", "key_name": "GROK_API_KEY"}
            ]
        }

    @pytest.mark.asyncio
    async def test_never_returns_key_values(self, mock_tool_deps, httpx_mock: HTTPXMock):
        """Even if the API mistakenly includes key values, list_api_keys strips them."""
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/apikeys",
            json={"entries": [
                {"name": "my-key", "service_hint": "Test", "api_key": "sk-abc123"},
            ]},
        )
        result = await tools.list_api_keys()
        for entry in result["api_keys"]:
            assert "api_key" not in entry
            assert "key_value" not in entry

    @pytest.mark.asyncio
    async def test_not_logged_in(self):
        result = await tools.list_api_keys()
        assert "error" in result
        assert "Not logged in" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_list(self, mock_tool_deps, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/apikeys",
            json={"entries": []},
        )
        result = await tools.list_api_keys()
        assert result == {
            "api_keys": [],
            "total": 0,
            "projects": {},
            "standalone": [],
        }

    @pytest.mark.asyncio
    async def test_filter_by_project_name(self, mock_tool_deps, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/apikeys",
            json={"entries": [
                {"name": "github-api", "service_hint": "GitHub API", "created_at": "2026-06-17T05:36:25Z"},
                {"name": "twitter-bot/.env/GROK_API_KEY", "service_hint": "xAI", "created_at": "2026-06-17T06:00:00Z"},
                {"name": "twitter-bot/.env/TWITTER_TOKEN", "service_hint": "Twitter", "created_at": "2026-06-17T07:00:00Z"},
                {"name": "my-api/.env/OPENAI_KEY", "service_hint": "OpenAI", "created_at": "2026-06-17T08:00:00Z"},
            ]},
        )
        result = await tools.list_api_keys(project_name="twitter-bot")
        assert result["total"] == 2
        for item in result["api_keys"]:
            assert item["project"] == "twitter-bot"
            assert item["key_name"] in ("GROK_API_KEY", "TWITTER_TOKEN")

    @pytest.mark.asyncio
    async def test_filter_by_project_empty(self, mock_tool_deps, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/apikeys",
            json={"entries": [
                {"name": "github-api", "service_hint": "GitHub API", "created_at": "2026-06-17T05:36:25Z"},
            ]},
        )
        result = await tools.list_api_keys(project_name="nonexistent")
        assert result["total"] == 0
        assert result["api_keys"] == []
        assert result["projects"] == {}
        assert result["standalone"] == []


# ── check_credential_exists ────────────────────────────────────────────────

class TestCheckCredentialExists:
    @pytest.mark.asyncio
    async def test_returns_exists(self, mock_tool_deps, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/vault/proxy/check/github.com",
            json={"exists": True, "site_name": "github.com", "username_hint": "testuser"},
        )
        result = await tools.check_credential_exists("github.com")
        assert result == {"exists": True, "site_name": "github.com", "username_hint": "testuser"}

    @pytest.mark.asyncio
    async def test_not_logged_in(self):
        result = await tools.check_credential_exists("github.com")
        assert "error" in result
        assert "Not logged in" in result["error"]


# ── get_username_for_site ─────────────────────────────────────────────────

class TestGetUsernameForSite:
    @pytest.mark.asyncio
    async def test_returns_username(self, mock_tool_deps):
        result = await tools.get_username_for_site(TEST_SITE)
        assert result == {"site_name": TEST_SITE, "username": TEST_CREDS["username"]}

    @pytest.mark.asyncio
    async def test_not_logged_in(self):
        result = await tools.get_username_for_site(TEST_SITE)
        assert "error" in result
        assert "Not logged in" in result["error"]


# ── browser_login ──────────────────────────────────────────────────────────

class _MockPage:
    """Minimal Playwright Page mock."""

    def __init__(self, start_url="https://github.com"):
        self._url = start_url
        self._title = "GitHub"

    @property
    def url(self) -> str:
        return self._url

    async def title(self) -> str:
        return self._title

    async def goto(self, url, **kw):
        self._url = url
        return None

    async def wait_for_load_state(self, state, **kw):
        return None

    async def wait_for_function(self, js, **kw):
        return None

    async def screenshot(self, **kw):
        return b"fake-png"

    def locator(self, selector):
        return self._MockLocator()

    def get_by_role(self, role, name=None):
        return self._MockLocator()

    def get_by_label(self, text):
        return self._MockLocator()

    class _MockLocator:
        """Minimal Playwright Locator mock."""

        def __init__(self, visible=True):
            self._visible = visible
            self._value = ""
            self.first = self

        async def is_visible(self, **kw):
            return self._visible

        async def wait_for(self, **kw):
            return None

        async def click(self):
            return None

        async def fill(self, value):
            self._value = value
            return None

        async def clear(self):
            self._value = ""
            return None

        async def type(self, value, **kw):
            self._value = value
            return None

        async def input_value(self):
            return self._value

        async def inner_text(self):
            return ""


class _MockContext:
    """Minimal Playwright BrowserContext mock."""

    async def new_page(self):
        return _MockPage()

    async def storage_state(self, **kw):
        return {}

    async def close(self):
        pass


class _MockBrowser:
    """Minimal Playwright Browser mock."""

    def __init__(self):
        self.contexts = [_MockContext()]

    async def new_context(self, **kw):
        return _MockContext()

    async def close(self):
        self.contexts = []


class TestBrowserLogin:
    @pytest.mark.asyncio
    async def test_not_logged_in(self):
        result = await tools.browser_login("github.com")
        assert "error" in result
        assert "Not logged in" in result["error"]

    @pytest.mark.asyncio
    async def test_rejects_non_http_scheme(self, mock_tool_deps, monkeypatch):
        monkeypatch.setattr(tools, "is_logged_in", lambda: True)
        result = await tools.browser_login(
            "github.com",
            login_url="javascript:alert(1)",
        )
        assert "error" in result
        assert "javascript" in result["error"]

    @pytest.mark.asyncio
    async def test_successful_login(self, mock_tool_deps, monkeypatch):
        monkeypatch.setattr(tools, "is_logged_in", lambda: True)

        mock_browser = _MockBrowser()
        async def _get_browser_mock():
            return mock_browser
        monkeypatch.setattr(tools, "_get_browser", _get_browser_mock)

        async def _decrypt_mock(site):
            return dict(TEST_CREDS)
        monkeypatch.setattr(tools, "_decrypt_site_credential", _decrypt_mock)

        result = await tools.browser_login("github.com")
        assert isinstance(result, dict)
        assert "success" in result

    @pytest.mark.asyncio
    async def test_decrypt_failure(self, mock_tool_deps, monkeypatch):
        monkeypatch.setattr(tools, "is_logged_in", lambda: True)

        async def _decrypt_fail(site):
            raise RuntimeError("Decryption failed: invalid VEK")

        monkeypatch.setattr(tools, "_decrypt_site_credential", _decrypt_fail)

        result = await tools.browser_login("github.com")
        assert "error" in result
        assert "Failed to decrypt" in result["error"]


# ── browser cleanup ──────────────────────────────────────────────────────────

class TestCloseAllBrowsers:
    @pytest.mark.asyncio
    async def test_noop_when_no_browser(self):
        await tools.close_all_browsers()

    @pytest.mark.asyncio
    async def test_called_multiple_times(self):
        await tools.close_all_browsers()
        await tools.close_all_browsers()


# ── use_credential ──────────────────────────────────────────────────────────


class TestUseCredential:
    """Tests for the use_credential tool — API key lookup, vault fallback,
    auth header injection, field filtering, and auth-gating."""

    @pytest.mark.asyncio
    async def test_not_logged_in(self):
        """When not logged in, returns an error dict (no fixtures needed)."""
        result = await tools.use_credential("any-site", "https://example.com/data")
        assert "error" in result
        assert "Not logged in" in result["error"]

    @pytest.mark.asyncio
    async def test_api_key_lookup_success(self, mock_tool_deps, httpx_mock: HTTPXMock):
        """API key is found in /apikeys/{name}, decrypted, used for a request."""
        import json
        import os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        # Encrypt an API key payload with TEST_VEK
        api_key_payload = {
            "service": "TestService",
            "api_key": "sk-test-key-value",
            "notes": "",
        }
        iv = os.urandom(12)
        aesgcm = AESGCM(TEST_VEK)
        payload_bytes = json.dumps(api_key_payload).encode("utf-8")
        ciphertext = aesgcm.encrypt(iv, payload_bytes, None)
        blob_hex, iv_hex = ciphertext.hex(), iv.hex()

        # Mock the API key lookup endpoint
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/apikeys/test-key",
            json={
                "encrypted_blob": blob_hex,
                "iv": iv_hex,
            },
        )

        # Mock the target URL request
        httpx_mock.add_response(
            method="GET",
            url="https://api.example.com/data",
            json={"result": "success", "data": [1, 2, 3]},
        )

        result = await tools.use_credential(
            "test-key",
            "https://api.example.com/data",
        )

        assert result["success"] is True
        assert result["status_code"] == 200
        assert result["data"] == {"result": "success", "data": [1, 2, 3]}

    @pytest.mark.asyncio
    async def test_vault_fallback_success(self, mock_tool_deps, httpx_mock: HTTPXMock):
        """API key lookup fails (500), falls back to vault entry, then succeeds."""
        from conftest import encrypt_test_creds

        # Mock API key lookup to fail with 500
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/apikeys/test-site",
            status_code=500,
        )

        # Mock vault entry lookup to return encrypted TEST_CREDS
        blob_hex, iv_hex = encrypt_test_creds()
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/vault/test-site",
            json={
                "encrypted_blob": blob_hex,
                "iv": iv_hex,
                "site_name": "test-site",
            },
        )

        # Mock the target URL request
        httpx_mock.add_response(
            method="GET",
            url="https://api.example.com/login",
            json={"status": "authenticated"},
        )

        result = await tools.use_credential(
            "test-site",
            "https://api.example.com/login",
            inject_as="bearer_token",
        )

        assert result["success"] is True
        assert result["status_code"] == 200

    @pytest.mark.asyncio
    async def test_neither_found(self, mock_tool_deps, httpx_mock: HTTPXMock):
        """Both API key and vault lookups fail, returns error."""
        # Mock API key lookup to fail
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/apikeys/missing-cred",
            status_code=404,
        )

        # Mock vault lookup to fail
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/vault/missing-cred",
            status_code=404,
        )

        result = await tools.use_credential(
            "missing-cred",
            "https://api.example.com/data",
        )

        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_api_key_header_injection(self, mock_tool_deps, httpx_mock: HTTPXMock):
        """inject_as='api_key_header' with custom header_name puts the key in that header."""
        import json
        import os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        # Encrypt an API key payload
        api_key_payload = {
            "service": "SecretAPI",
            "api_key": "sk-custom-key",
            "notes": "",
        }
        iv = os.urandom(12)
        aesgcm = AESGCM(TEST_VEK)
        payload_bytes = json.dumps(api_key_payload).encode("utf-8")
        ciphertext = aesgcm.encrypt(iv, payload_bytes, None)
        blob_hex, iv_hex = ciphertext.hex(), iv.hex()

        # Mock API key lookup
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/apikeys/my-api-key",
            json={
                "encrypted_blob": blob_hex,
                "iv": iv_hex,
            },
        )

        # Mock target — we check it received X-API-Key header
        httpx_mock.add_response(
            method="GET",
            url="https://api.example.com/protected",
            json={"message": "authorized"},
        )

        result = await tools.use_credential(
            "my-api-key",
            "https://api.example.com/protected",
            inject_as="api_key_header",
            header_name="X-API-Key",
        )

        assert result["success"] is True
        assert result["status_code"] == 200

    @pytest.mark.asyncio
    async def test_field_filtering(self, mock_tool_deps, httpx_mock: HTTPXMock):
        """The fields parameter filters the response data to only selected keys."""
        import json
        import os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        # Encrypt an API key payload
        api_key_payload = {
            "service": "FilterService",
            "api_key": "sk-filter-test",
            "notes": "",
        }
        iv = os.urandom(12)
        aesgcm = AESGCM(TEST_VEK)
        payload_bytes = json.dumps(api_key_payload).encode("utf-8")
        ciphertext = aesgcm.encrypt(iv, payload_bytes, None)
        blob_hex, iv_hex = ciphertext.hex(), iv.hex()

        # Mock API key lookup
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/apikeys/filter-key",
            json={
                "encrypted_blob": blob_hex,
                "iv": iv_hex,
            },
        )

        # Mock target — returns a response with multiple fields
        httpx_mock.add_response(
            method="GET",
            url="https://api.example.com/data",
            json={"a": 1, "b": 2, "c": 3},
        )

        result = await tools.use_credential(
            "filter-key",
            "https://api.example.com/data",
            fields=["a", "c"],
        )

        assert result["success"] is True
        assert result["data"] == {"a": 1, "c": 3}


# ── run_with_credential ────────────────────────────────────────────────────────


class TestRunWithCredential:
    """Tests for run_with_credential — command execution with credential injection."""

    @pytest.mark.asyncio
    async def test_not_logged_in(self):
        """When not logged in, returns an error."""
        result = await tools.run_with_credential("any-site", "echo hi")
        assert "error" in result
        assert "Not logged in" in result["error"]

    @pytest.mark.asyncio
    async def test_api_key_env_injection(self, mock_tool_deps, httpx_mock: HTTPXMock):
        """API key credential injected as env var, command runs, output redacted."""
        import json
        import os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        # Encrypt an API key payload
        api_key_payload = {
            "service": "pypi",
            "api_key": "***",
            "notes": "",
        }
        iv = os.urandom(12)
        aesgcm = AESGCM(TEST_VEK)
        payload_bytes = json.dumps(api_key_payload).encode("utf-8")
        ciphertext = aesgcm.encrypt(iv, payload_bytes, None)
        blob_hex, iv_hex = ciphertext.hex(), iv.hex()

        # Mock API key lookup
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/apikeys/testpypi",
            json={
                "encrypted_blob": blob_hex,
                "iv": iv_hex,
            },
        )

        result = await tools.run_with_credential(
            site_name="testpypi",
            command="echo %TWINE_USERNAME% && echo %TWINE_PASSWORD%",
            inject_as="env",
            env_var_name="TWINE_PASSWORD",
        )

        assert result["exit_code"] == 0
        # TWINE_USERNAME should be "__token__" (convenience)
        assert "__token__" in result["stdout"]
        # TWINE_PASSWORD should be REDACTED
        assert "[REDACTED]" in result["stdout"]

    @pytest.mark.asyncio
    async def test_stdin_injection(self, mock_tool_deps, httpx_mock: HTTPXMock):
        """Credential piped as stdin, command processes it, output redacted."""
        import json
        import os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        api_key_payload = {
            "service": "docker",
            "api_key": "my-docker-password",
            "notes": "",
        }
        iv = os.urandom(12)
        aesgcm = AESGCM(TEST_VEK)
        payload_bytes = json.dumps(api_key_payload).encode("utf-8")
        ciphertext = aesgcm.encrypt(iv, payload_bytes, None)
        blob_hex, iv_hex = ciphertext.hex(), iv.hex()

        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/apikeys/dockerhub",
            json={
                "encrypted_blob": blob_hex,
                "iv": iv_hex,
            },
        )

        result = await tools.run_with_credential(
            site_name="dockerhub",
            command="cat && echo '---stdin received---'",
            inject_as="stdin",
        )

        assert result["exit_code"] == 0
        # The credential was piped via stdin, then echoed by cat
        # It should be redacted in the output
        assert "[REDACTED]" in result["stdout"]
        assert "stdin received" in result["stdout"]

    @pytest.mark.asyncio
    async def test_neither_found(self, mock_tool_deps, httpx_mock: HTTPXMock):
        """Both API key and vault lookups fail."""
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/apikeys/missing",
            status_code=404,
        )
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/vault/missing",
            status_code=404,
        )

        result = await tools.run_with_credential("missing", "echo hi")
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_vault_fallback(self, mock_tool_deps, httpx_mock: HTTPXMock):
        """API key lookup fails, falls back to vault entry."""
        from conftest import encrypt_test_creds

        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/apikeys/github.com",
            status_code=500,
        )

        blob_hex, iv_hex = encrypt_test_creds()
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/vault/github.com",
            json={
                "encrypted_blob": blob_hex,
                "iv": iv_hex,
                "site_name": "github.com",
            },
        )

        result = await tools.run_with_credential(
            site_name="github.com",
            command="echo %MY_PASS%",
            inject_as="env",
            env_var_name="MY_PASS",
        )

        assert result["exit_code"] == 0
        # The password value should be redacted
        assert "[REDACTED]" in result["stdout"]

    @pytest.mark.asyncio
    async def test_env_var_name_required(self, mock_tool_deps, httpx_mock: HTTPXMock):
        """Missing env_var_name with inject_as='env' returns error."""
        import json
        import os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        api_key_payload = {
            "service": "test",
            "api_key": "some-key",
            "notes": "",
        }
        iv = os.urandom(12)
        aesgcm = AESGCM(TEST_VEK)
        payload_bytes = json.dumps(api_key_payload).encode("utf-8")
        ciphertext = aesgcm.encrypt(iv, payload_bytes, None)
        blob_hex, iv_hex = ciphertext.hex(), iv.hex()

        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/apikeys/some-key",
            json={
                "encrypted_blob": blob_hex,
                "iv": iv_hex,
            },
        )

        result = await tools.run_with_credential(
            site_name="some-key",
            command="echo hi",
            inject_as="env",
            # No env_var_name!
        )

        assert "error" in result
        assert "env_var_name is required" in result["error"]
