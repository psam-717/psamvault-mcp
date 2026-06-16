"""Tests for tools.py — MCP tools exposed to AI agents."""

import asyncio
import json

import pytest
from pytest_httpx import HTTPXMock

from unittest.mock import AsyncMock

import sys as _sys
import os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from conftest import TEST_ACCESS_TOKEN, TEST_CREDS, TEST_SITE

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
