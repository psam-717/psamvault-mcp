"""Tests for tools.py — MCP tools exposed to AI agents."""

import pytest
from pytest_httpx import HTTPXMock

from unittest.mock import AsyncMock

from mcp_server import api_client, tools
from tests.conftest import TEST_ACCESS_TOKEN, TEST_CREDS, TEST_SITE


# ── _filter_response (pure function, no async needed) ──────────────────────

class TestFilterResponse:
    def test_dict_filtering(self):
        data = {"a": 1, "b": 2, "c": 3}
        assert tools._filter_response(data, ["a", "c"]) == {"a": 1, "c": 3}

    def test_list_of_dicts_filtering(self):
        data = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
        assert tools._filter_response(data, ["a"]) == [{"a": 1}, {"a": 3}]

    def test_empty_fields_returns_unchanged(self):
        data = {"a": 1}
        assert tools._filter_response(data, None) is data
        assert tools._filter_response(data, []) is data

    def test_non_dict_or_list_passthrough(self):
        assert tools._filter_response("hello", ["a"]) == "hello"
        assert tools._filter_response(42, ["a"]) == 42


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
        # is_logged_in returns False in default test environment (no session_file fixture)
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

    @pytest.mark.asyncio
    async def test_consent_denied(self, mock_session, monkeypatch):
        import mcp_server.tools as t

        async def _deny(*a, **kw):
            return False

        monkeypatch.setattr(t, "_request_consent_async", _deny)
        monkeypatch.setattr(t, "is_logged_in", lambda: True)
        result = await t.get_username_for_site(TEST_SITE)
        assert "error" in result
        assert "Access denied" in result["error"]


# ── use_credential ─────────────────────────────────────────────────────────

class TestUseCredential:
    @pytest.mark.asyncio
    async def test_success_flow(self, mock_tool_deps, httpx_mock: HTTPXMock, monkeypatch):
        """Full success flow: consent → decrypt → proxy → filtered response."""
        proxy_response = {
            "status_code": 200,
            "response_body": {"login": "testuser", "id": 123},
            "site_name": "github.com",
            "injected_as": "bearer_token",
            "target_url": "https://api.github.com/user",
        }
        httpx_mock.add_response(
            method="POST",
            url=f"{api_client.BASE_URL}/vault/proxy",
            json=proxy_response,
        )
        # Suppress notify_completion (just prints to stderr)
        monkeypatch.setattr("mcp_server.consent.notify_completion", lambda *a, **kw: None)

        result = await tools.use_credential(
            site_name="github.com",
            target_url="https://api.github.com/user",
            method="GET",
            inject_as="bearer_token",
        )
        assert result["status_code"] == 200
        assert result["site_name"] == "github.com"
        assert result["fields_applied"] is None

    @pytest.mark.asyncio
    async def test_rejects_non_http_scheme(self, mock_tool_deps):
        """target_url with non-http scheme is rejected."""
        result = await tools.use_credential(
            site_name="github.com",
            target_url="file:///etc/passwd",
            method="GET",
            inject_as="bearer_token",
        )
        assert "error" in result
        assert "file" in result["error"]

    @pytest.mark.asyncio
    async def test_consent_denied(self, mock_session, monkeypatch):
        """Returns error when user denies consent."""
        import mcp_server.tools as t

        async def _deny(*a, **kw):
            return False

        monkeypatch.setattr(t, "_request_consent_async", _deny)
        monkeypatch.setattr(t, "is_logged_in", lambda: True)
        result = await t.use_credential(
            site_name="github.com",
            target_url="https://api.github.com/user",
            method="GET",
            inject_as="bearer_token",
        )
        assert "error" in result
        assert "Access denied" in result["error"]

    @pytest.mark.asyncio
    async def test_not_logged_in(self):
        result = await tools.use_credential(
            site_name="github.com",
            target_url="https://api.github.com/user",
            method="GET",
            inject_as="bearer_token",
        )
        assert "error" in result
        assert "Not logged in" in result["error"]

    @pytest.mark.asyncio
    async def test_with_fields_filtering(
        self, mock_tool_deps, httpx_mock: HTTPXMock, monkeypatch
    ):
        """Fields parameter trims response_body to requested keys."""
        proxy_response = {
            "status_code": 200,
            "response_body": {"login": "testuser", "id": 123, "avatar_url": "..."},
            "site_name": "github.com",
            "injected_as": "bearer_token",
            "target_url": "https://api.github.com/user",
        }
        httpx_mock.add_response(
            method="POST",
            url=f"{api_client.BASE_URL}/vault/proxy",
            json=proxy_response,
        )
        monkeypatch.setattr("mcp_server.consent.notify_completion", lambda *a, **kw: None)

        result = await tools.use_credential(
            site_name="github.com",
            target_url="https://api.github.com/user",
            method="GET",
            inject_as="bearer_token",
            fields=["login", "id"],
        )
        assert result["fields_applied"] == ["login", "id"]
        assert result["response_body"] == {"login": "testuser", "id": 123}


# ── browser_login ──────────────────────────────────────────────────────────

class TestBrowserLogin:
    @pytest.mark.asyncio
    async def test_not_logged_in(self):
        result = await tools.browser_login("github.com")
        assert "error" in result
        assert "Not logged in" in result["error"]

    @pytest.mark.asyncio
    async def test_rejects_non_http_scheme(self, mock_tool_deps):
        """login_url with non-http scheme is rejected."""
        result = await tools.browser_login(
            "github.com",
            login_url="javascript:alert(1)",
        )
        assert "error" in result
        assert "javascript" in result["error"]


# ── browser cleanup ──────────────────────────────────────────────────────────

class TestCloseAllBrowsers:
    @pytest.mark.asyncio
    async def test_closes_tracked_browsers(self):
        """close_all_browsers calls close() on each tracked browser and clears the set."""
        mock_browser = AsyncMock()
        tools._ACTIVE_BROWSERS.add(mock_browser)

        await tools.close_all_browsers()

        mock_browser.close.assert_awaited_once()
        assert len(tools._ACTIVE_BROWSERS) == 0

    @pytest.mark.asyncio
    async def test_handles_close_error_gracefully(self):
        """A browser whose close() raises is still removed from the set."""
        mock_browser = AsyncMock()
        mock_browser.close.side_effect = RuntimeError("browser crash")
        tools._ACTIVE_BROWSERS.add(mock_browser)

        await tools.close_all_browsers()

        mock_browser.close.assert_awaited_once()
        assert len(tools._ACTIVE_BROWSERS) == 0

    @pytest.mark.asyncio
    async def test_empty_set_is_noop(self):
        """close_all_browsers does nothing when no browsers are tracked."""
        await tools.close_all_browsers()  # should not raise