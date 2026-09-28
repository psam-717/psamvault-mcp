"""Tests for api_client.py — async HTTP client for psamvault API."""

import httpx
import pytest
from pytest_httpx import HTTPXMock

from mcp_server import api_client
import sys as _sys
import os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from conftest import TEST_ACCESS_TOKEN, TEST_REFRESH_TOKEN


class TestAuthHeaders:
    def test_returns_bearer_token(self):
        headers = api_client._auth_headers("mytoken")
        assert headers == {"Authorization": "Bearer mytoken"}


class TestHandleError:
    def test_raises_on_non_success(self):
        response = httpx.Response(404)
        with pytest.raises(RuntimeError, match="psamvault API error 404"):
            api_client._handle_error(response)

    def test_passes_on_success(self):
        response = httpx.Response(200)
        api_client._handle_error(response)  # should not raise

    def test_includes_detail_from_json(self):
        response = httpx.Response(400, json={"detail": "bad request"})
        with pytest.raises(RuntimeError, match="bad request"):
            api_client._handle_error(response)


class TestListVaultEntries:
    @pytest.mark.asyncio
    async def test_returns_entries(self, httpx_mock: HTTPXMock, session_file):
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/vault",
            json={"entries": [{"site_name": "github.com", "username_hint": "testuser"}]},
        )
        result = await api_client.list_vault_entries(TEST_ACCESS_TOKEN)
        assert result == [{"site_name": "github.com", "username_hint": "testuser"}]

    @pytest.mark.asyncio
    async def test_401_triggers_refresh(self, httpx_mock: HTTPXMock, session_file):
        """A 401 response triggers token refresh and retries the request."""
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/vault",
            status_code=401,
        )
        httpx_mock.add_response(
            method="POST",
            url=f"{api_client.BASE_URL}/auth/refresh",
            json={"access_token": "new_access", "refresh_token": "new_refresh"},
        )
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/vault",
            json={"entries": [{"site_name": "github.com", "username_hint": "testuser"}]},
        )
        result = await api_client.list_vault_entries(TEST_ACCESS_TOKEN)
        assert result == [{"site_name": "github.com", "username_hint": "testuser"}]

    @pytest.mark.asyncio
    async def test_persistent_401_raises_runtime_error(
        self, httpx_mock: HTTPXMock, session_file
    ):
        """When refresh also fails, a Session expired error is raised."""
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/vault",
            status_code=401,
        )
        httpx_mock.add_response(
            method="POST",
            url=f"{api_client.BASE_URL}/auth/refresh",
            status_code=401,
        )
        with pytest.raises(RuntimeError, match="Session expired"):
            await api_client.list_vault_entries(TEST_ACCESS_TOKEN)

    @pytest.mark.asyncio
    async def test_retries_with_a_token_rotated_by_another_client(
        self, httpx_mock: HTTPXMock, session_file, monkeypatch
    ):
        """Refresh tokens are single-use and every client shares one store.

        When our refresh 401s because another process rotated the chain first, the
        stored token has changed: re-read it and retry instead of reporting expiry.
        """
        from mcp_server import session as session_mod

        attempts: list[str] = []

        async def _refresh(token: str):
            attempts.append(token)
            if len(attempts) == 1:
                # another client rotated the chain and persisted its successor
                session_mod.update_tokens("winner_access", "rotated_by_other_client")
                raise RuntimeError(
                    "psamvault API error 401: Refresh token is invalid or has expired"
                )
            return ("new_access", "new_refresh")

        monkeypatch.setattr(api_client, "_refresh_access_token", _refresh)
        httpx_mock.add_response(
            method="GET", url=f"{api_client.BASE_URL}/vault", status_code=401
        )
        httpx_mock.add_response(
            method="GET", url=f"{api_client.BASE_URL}/vault", json={"entries": []}
        )

        result = await api_client.list_vault_entries(TEST_ACCESS_TOKEN)

        assert result == []
        assert attempts == [TEST_REFRESH_TOKEN, "rotated_by_other_client"]

    @pytest.mark.asyncio
    async def test_does_not_retry_when_the_store_is_unchanged(
        self, httpx_mock: HTTPXMock, session_file, monkeypatch
    ):
        """A genuinely dead chain gets one attempt, not a retry loop."""
        attempts: list[str] = []

        async def _refresh(token: str):
            attempts.append(token)
            raise RuntimeError(
                "psamvault API error 401: Refresh token is invalid or has expired"
            )

        monkeypatch.setattr(api_client, "_refresh_access_token", _refresh)
        httpx_mock.add_response(
            method="GET", url=f"{api_client.BASE_URL}/vault", status_code=401
        )

        with pytest.raises(RuntimeError, match="Session expired"):
            await api_client.list_vault_entries(TEST_ACCESS_TOKEN)

        assert attempts == [TEST_REFRESH_TOKEN]


class TestGetVaultEntry:
    @pytest.mark.asyncio
    async def test_returns_entry(self, httpx_mock: HTTPXMock, session_file):
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/vault/github.com",
            json={"encrypted_blob": "abc123", "iv": "iv123"},
        )
        result = await api_client.get_vault_entry(TEST_ACCESS_TOKEN, "github.com")
        assert result == {"encrypted_blob": "abc123", "iv": "iv123"}


class TestCheckSiteExists:
    @pytest.mark.asyncio
    async def test_returns_exists(self, httpx_mock: HTTPXMock, session_file):
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/vault/proxy/check/github.com",
            json={"exists": True, "site_name": "github.com", "username_hint": "testuser"},
        )
        result = await api_client.check_site_exists(TEST_ACCESS_TOKEN, "github.com")
        assert result == {"exists": True, "site_name": "github.com", "username_hint": "testuser"}


class TestUpdateVaultEntryUrl:
    @pytest.mark.asyncio
    async def test_updates_login_url(self, httpx_mock: HTTPXMock, session_file):
        httpx_mock.add_response(
            method="PUT",
            url=f"{api_client.BASE_URL}/vault/github.com",
            json={"site_name": "github.com", "login_url": "https://github.com/login"},
        )
        result = await api_client.update_vault_entry_url(
            TEST_ACCESS_TOKEN, "github.com", "https://github.com/login",
        )
        assert result == {"site_name": "github.com", "login_url": "https://github.com/login"}


class TestProxyRequest:
    pass  # proxy_request removed — use_credential tool was removed from the MCP
