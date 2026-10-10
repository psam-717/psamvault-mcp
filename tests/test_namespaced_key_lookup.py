"""Issue #47 — a name stored by ``scan_and_protect`` must be readable again by the other tools.

``scan_and_protect`` stores an API key under a namespaced name (``project/.env/KEY``, or the
unscoped ``env/.env/KEY``) and ``list_api_keys`` returns that exact string. The read side used to
hand whatever the caller typed straight to ``GET /apikeys/{name}``:

* the full stored name 404'd, because the lookup did not survive as one path parameter; and
* a bare leaf (``KEY``) 404'd, because the *stored* name is the namespaced one — the API then said
  "No API key entry found for 'KEY'", which reads like the key does not exist while
  ``list_api_keys`` is listing it.

These tests pin the contract from the issue:

* the exact ``name`` string ``list_api_keys`` returns works, slashes intact;
* a leaf matching exactly one stored key resolves to that stored name;
* a leaf matching several stored keys is an ERROR naming the candidates — never a 404, never a
  guess, and never a single-key GET with the leaf;
* a name matching nothing stays a not-found error.

No network: the list read and the entry read are served by the suite's pytest-httpx fakes.
"""

import json
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pytest_httpx import HTTPXMock

from conftest import TEST_ACCESS_TOKEN, TEST_VEK

from mcp_server import api_client, tools

SECRET = "pk_live_namespaced_secret_4711"

# The two shapes ``scan_and_protect`` actually writes.  The project-scoped one is what issue #47
# reports verbatim (`atlas054probe/.env/my_custom_key`); the unscoped one is the real
# `env/.env/telegram_bot_token` entry the scan produced over HERMES_HOME/.env.
PROJECT_KEY = "atlas054probe/.env/my_custom_key"
ENV_KEY = "env/.env/telegram_bot_token"

VERIFY_URL = "https://api.acme-internal.test/whoami"


def _encrypted_entry(service: str = "acme-internal") -> dict:
    """An API-key payload encrypted with TEST_VEK, shaped like GET /apikeys/{name}."""
    iv = _os.urandom(12)
    payload = json.dumps({"service": service, "api_key": SECRET, "notes": None}).encode("utf-8")
    ciphertext = AESGCM(TEST_VEK).encrypt(iv, payload, None)
    return {"encrypted_blob": ciphertext.hex(), "iv": iv.hex()}


def _list_body(*names: str) -> dict:
    return {
        "entries": [
            {"name": name, "service_hint": None, "notes": None, "created_at": None}
            for name in names
        ]
    }


def _add_list(httpx_mock: HTTPXMock, *names: str) -> None:
    httpx_mock.add_response(method="GET", url=f"{api_client.BASE_URL}/apikeys", json=_list_body(*names))


def _paths(httpx_mock: HTTPXMock) -> list[str]:
    return [request.url.path for request in httpx_mock.get_requests()]


# ── the resolver ──────────────────────────────────────────────────────────────


class TestFullStoredName:
    """The exact string list_api_keys returns is passed straight through, slashes and all."""

    @pytest.mark.asyncio
    async def test_project_scoped_name_is_looked_up_verbatim(
        self, httpx_mock: HTTPXMock, session_file
    ):
        entry = _encrypted_entry()
        httpx_mock.add_response(
            method="GET", url=f"{api_client.BASE_URL}/apikeys/{PROJECT_KEY}", json=entry
        )

        result = await api_client.get_api_key_entry(TEST_ACCESS_TOKEN, PROJECT_KEY)

        assert result == entry
        assert _paths(httpx_mock) == [f"/apikeys/{PROJECT_KEY}"]
        # The slashes must reach the server as path separators: percent-encoding them would make
        # the route miss the entry that list_api_keys just returned.
        assert httpx_mock.get_requests()[0].url.raw_path == b"/apikeys/atlas054probe/.env/my_custom_key"

    @pytest.mark.asyncio
    async def test_unscoped_name_is_looked_up_verbatim(
        self, httpx_mock: HTTPXMock, session_file
    ):
        entry = _encrypted_entry()
        httpx_mock.add_response(
            method="GET", url=f"{api_client.BASE_URL}/apikeys/{ENV_KEY}", json=entry
        )

        result = await api_client.get_api_key_entry(TEST_ACCESS_TOKEN, ENV_KEY)

        assert result == entry
        assert _paths(httpx_mock) == [f"/apikeys/{ENV_KEY}"]


class TestLeafResolution:
    @pytest.mark.asyncio
    async def test_unique_leaf_resolves_to_the_stored_name(
        self, httpx_mock: HTTPXMock, session_file
    ):
        entry = _encrypted_entry()
        _add_list(httpx_mock, PROJECT_KEY)
        httpx_mock.add_response(
            method="GET", url=f"{api_client.BASE_URL}/apikeys/{PROJECT_KEY}", json=entry
        )

        result = await api_client.get_api_key_entry(TEST_ACCESS_TOKEN, "my_custom_key")

        assert result == entry
        # List first, then the RESOLVED name — never the bare leaf.
        assert _paths(httpx_mock) == [
            "/apikeys",
            f"/apikeys/{PROJECT_KEY}",
        ]

    @pytest.mark.asyncio
    async def test_standalone_key_resolves_to_its_own_name(
        self, httpx_mock: HTTPXMock, session_file
    ):
        """A flat key stored by name (``pypi``) keeps working, matched exactly."""
        entry = _encrypted_entry(service="pypi")
        _add_list(httpx_mock, "pypi", PROJECT_KEY)
        httpx_mock.add_response(method="GET", url=f"{api_client.BASE_URL}/apikeys/pypi", json=entry)

        result = await api_client.get_api_key_entry(TEST_ACCESS_TOKEN, "pypi")

        assert result == entry
        assert _paths(httpx_mock) == ["/apikeys", "/apikeys/pypi"]


class TestAmbiguousLeaf:
    @pytest.mark.asyncio
    async def test_leaf_in_two_projects_is_an_ambiguous_error(
        self, httpx_mock: HTTPXMock, session_file
    ):
        _add_list(httpx_mock, "alpha/.env/shared_token", "beta/.env/shared_token")

        with pytest.raises(api_client.AmbiguousApiKeyError) as excinfo:
            await api_client.get_api_key_entry(TEST_ACCESS_TOKEN, "shared_token")

        message = str(excinfo.value)
        assert "ambiguous" in message.lower()
        assert "alpha/.env/shared_token" in message
        assert "beta/.env/shared_token" in message
        assert excinfo.value.candidates == ["alpha/.env/shared_token", "beta/.env/shared_token"]
        # The whole point: no single-key GET with the leaf, so the caller never sees the
        # "no API key entry found" 404 that states the opposite of the truth.
        assert _paths(httpx_mock) == ["/apikeys"]


class TestUnknownName:
    @pytest.mark.asyncio
    async def test_unknown_name_is_not_found(self, httpx_mock: HTTPXMock, session_file):
        _add_list(httpx_mock, PROJECT_KEY)

        with pytest.raises(api_client.ApiKeyLookupError) as excinfo:
            await api_client.get_api_key_entry(TEST_ACCESS_TOKEN, "nope")

        assert "not found" in str(excinfo.value).lower()
        assert "nope" in str(excinfo.value)
        assert _paths(httpx_mock) == ["/apikeys"]


# ── the round trip the docs promise: scan -> read the value back ──────────────


class TestToolsReadAScannedEntryBack:
    """``export_key_to_env_file`` / ``run_with_credential`` must address a scanned entry.

    This is the round trip AGENTS.md documents for handing a tool a credential without pasting it.
    """

    @pytest.mark.asyncio
    async def test_export_key_to_env_file_accepts_the_full_name(
        self, tmp_path, mock_tool_deps, httpx_mock: HTTPXMock
    ):
        env_file = tmp_path / ".env"
        httpx_mock.add_response(
            method="GET", url=f"{api_client.BASE_URL}/apikeys/{ENV_KEY}", json=_encrypted_entry()
        )
        httpx_mock.add_response(url=VERIFY_URL, status_code=200)

        result = await tools.export_key_to_env_file(
            key_name=ENV_KEY,
            env_var_name="TELEGRAM_BOT_TOKEN",
            env_path=str(env_file),
            verify_url=VERIFY_URL,
        )

        assert result.get("success") is True, result
        assert "TELEGRAM_BOT_TOKEN=" + SECRET in env_file.read_text(encoding="utf-8")
        assert SECRET not in json.dumps(result)

    @pytest.mark.asyncio
    async def test_export_key_to_env_file_resolves_a_leaf(
        self, tmp_path, mock_tool_deps, httpx_mock: HTTPXMock
    ):
        env_file = tmp_path / ".env"
        _add_list(httpx_mock, PROJECT_KEY)
        httpx_mock.add_response(
            method="GET", url=f"{api_client.BASE_URL}/apikeys/{PROJECT_KEY}", json=_encrypted_entry()
        )
        httpx_mock.add_response(url=VERIFY_URL, status_code=200)

        result = await tools.export_key_to_env_file(
            key_name="my_custom_key",
            env_var_name="MY_CUSTOM_KEY",
            env_path=str(env_file),
            verify_url=VERIFY_URL,
        )

        assert result.get("success") is True, result
        assert "MY_CUSTOM_KEY=" + SECRET in env_file.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_run_with_credential_accepts_the_full_name(
        self, mock_tool_deps, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="GET", url=f"{api_client.BASE_URL}/apikeys/{PROJECT_KEY}", json=_encrypted_entry()
        )

        result = await tools.run_with_credential(
            site_name=PROJECT_KEY,
            command=_echo("MY_CUSTOM_KEY"),
            inject_as="env",
            env_var_name="MY_CUSTOM_KEY",
        )

        assert result["exit_code"] == 0, result
        assert SECRET not in result["stdout"]

    @pytest.mark.asyncio
    async def test_run_with_credential_resolves_a_leaf(
        self, mock_tool_deps, httpx_mock: HTTPXMock
    ):
        _add_list(httpx_mock, PROJECT_KEY)
        httpx_mock.add_response(
            method="GET", url=f"{api_client.BASE_URL}/apikeys/{PROJECT_KEY}", json=_encrypted_entry()
        )

        result = await tools.run_with_credential(
            site_name="my_custom_key",
            command=_echo("MY_CUSTOM_KEY"),
            inject_as="env",
            env_var_name="MY_CUSTOM_KEY",
        )

        assert result["exit_code"] == 0, result
        assert "[REDACTED]" in result["stdout"]

    @pytest.mark.asyncio
    async def test_run_with_credential_reports_ambiguity_not_not_found(
        self, mock_tool_deps, httpx_mock: HTTPXMock
    ):
        """An ambiguous leaf must not fall through the API-key leg into "not found"."""
        _add_list(httpx_mock, "alpha/.env/shared_token", "beta/.env/shared_token")

        result = await tools.run_with_credential(
            site_name="shared_token",
            command=_echo("SHARED_TOKEN"),
            inject_as="env",
            env_var_name="SHARED_TOKEN",
        )

        assert "error" in result
        assert "ambiguous" in result["error"].lower()
        assert "alpha/.env/shared_token" in result["error"]
        assert "beta/.env/shared_token" in result["error"]
        # No vault fallback, and no extra requests: the list read is the only call.
        assert _paths(httpx_mock) == ["/apikeys"]


def _echo(var: str) -> str:
    """Print an env var with the syntax of the shell behind ``shell=True`` (mirrors test_tools.py)."""
    return f"echo %{var}%" if _os.name == "nt" else f'echo "${var}"'
