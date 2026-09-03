"""Tests for the shared HTTP verify executor (mcp_server/verify.py).

Analysis:
- Unit under test: verify_key_http(key, url, method, expect, auth_kind, header_name).
- Inputs: decrypted API key + probe spec (from a recipe or an explicit
  verify_url override).
- Outputs: result dict {success, status, error_class, detail, probe_url}.
  The key value must NEVER appear in the result.
- Happy paths: probe returns the expected status -> success.
- Failure cases: 401/403 -> error_class "key_invalid"; 5xx/unexpected
  status -> "probe_unavailable"; network/timeout -> "probe_unavailable";
  unsupported auth_kind (basic_auth) -> ValueError.
- Mocked: all HTTP via pytest_httpx (handler records request headers so we
  can assert auth injection without hitting the network).
- Vault access is intentionally OUT of this module: the tool layer decrypts
  and passes the raw key, so this executor stays vault-agnostic and reusable.
"""

import json
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import httpx
import pytest
from pytest_httpx import HTTPXMock

# FAILING — mcp_server.verify not yet written
from mcp_server import verify

KEY = "test-secret-key-value-12345"
PROBE_URL = "https://api.render.com/v1/owners"


def _requested_auth_header(httpx_mock: HTTPXMock, header: str = "Authorization") -> str | None:
    request = httpx_mock.get_requests()[0]
    return request.headers.get(header)


class TestSuccess:
    @pytest.mark.asyncio
    async def test_expected_200_bearer__success_and_sends_bearer_header(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=PROBE_URL, status_code=200)

        result = await verify.verify_key_http(
            KEY, url=PROBE_URL, method="GET", expect=200, auth_kind="bearer"
        )

        assert result["success"] is True
        assert result["status"] == 200
        assert result["error_class"] is None
        assert _requested_auth_header(httpx_mock) == f"Bearer {KEY}"

    @pytest.mark.asyncio
    async def test_custom_expect_and_method__success(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=PROBE_URL, status_code=204)

        result = await verify.verify_key_http(
            KEY, url=PROBE_URL, method="HEAD", expect=204, auth_kind="bearer"
        )

        assert result["success"] is True
        assert result["status"] == 204
        assert httpx_mock.get_requests()[0].method == "HEAD"

    @pytest.mark.asyncio
    async def test_api_key_header_mode__sends_custom_header(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=PROBE_URL, status_code=200)

        result = await verify.verify_key_http(
            KEY,
            url=PROBE_URL,
            method="GET",
            expect=200,
            auth_kind="api_key_header",
            header_name="X-API-Key",
        )

        assert result["success"] is True
        assert _requested_auth_header(httpx_mock, "X-API-Key") == KEY


class TestKeyInvalid:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [401, 403])
    async def test_401_or_403__classified_key_invalid(self, httpx_mock: HTTPXMock, status_code):
        httpx_mock.add_response(url=PROBE_URL, status_code=status_code)

        result = await verify.verify_key_http(KEY, url=PROBE_URL, expect=200)

        assert result["success"] is False
        assert result["status"] == status_code
        assert result["error_class"] == "key_invalid"


class TestProbeUnavailable:
    @pytest.mark.asyncio
    async def test_5xx__classified_probe_unavailable(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=PROBE_URL, status_code=502)

        result = await verify.verify_key_http(KEY, url=PROBE_URL, expect=200)

        assert result["success"] is False
        assert result["status"] == 502
        assert result["error_class"] == "probe_unavailable"

    @pytest.mark.asyncio
    async def test_unexpected_2xx_status__classified_probe_unavailable(self, httpx_mock: HTTPXMock):
        # Server reachable and key accepted, but the response does not match
        # the recipe's expectation -> probe problem, not key problem.
        httpx_mock.add_response(url=PROBE_URL, status_code=201)

        result = await verify.verify_key_http(KEY, url=PROBE_URL, expect=200)

        assert result["success"] is False
        assert result["error_class"] == "probe_unavailable"

    @pytest.mark.asyncio
    async def test_network_error__classified_probe_unavailable(self, httpx_mock: HTTPXMock):
        httpx_mock.add_exception(httpx.ConnectError("connection refused"))

        result = await verify.verify_key_http(KEY, url=PROBE_URL, expect=200)

        assert result["success"] is False
        assert result["status"] is None
        assert result["error_class"] == "probe_unavailable"

    @pytest.mark.asyncio
    async def test_timeout__classified_probe_unavailable(self, httpx_mock: HTTPXMock):
        httpx_mock.add_exception(httpx.ReadTimeout("timed out"))

        result = await verify.verify_key_http(KEY, url=PROBE_URL, expect=200)

        assert result["success"] is False
        assert result["error_class"] == "probe_unavailable"


class TestSecurityContract:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [200, 401, 502])
    async def test_result_never_contains_key_value__success_and_failure(
        self, httpx_mock: HTTPXMock, status_code
    ):
        httpx_mock.add_response(url=PROBE_URL, status_code=status_code)
        result = await verify.verify_key_http(KEY, url=PROBE_URL, expect=200)
        assert KEY not in json.dumps(result), f"key leaked on status {status_code}"

    @pytest.mark.asyncio
    async def test_network_error_detail_never_contains_key(self, httpx_mock: HTTPXMock):
        httpx_mock.add_exception(httpx.ConnectError(f"dial failed for {KEY}"))

        result = await verify.verify_key_http(KEY, url=PROBE_URL, expect=200)

        assert KEY not in json.dumps(result)


class TestUnsupportedAuth:
    @pytest.mark.asyncio
    async def test_basic_auth__raises_value_error(self):
        # basic_auth raises before any HTTP request is made.
        with pytest.raises(ValueError, match="basic_auth"):
            await verify.verify_key_http(
                KEY, url=PROBE_URL, expect=200, auth_kind="basic_auth"
            )
