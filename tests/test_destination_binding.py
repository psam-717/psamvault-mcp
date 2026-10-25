"""Destination binding and response scrubbing for stored secrets.

These tests describe the boundary: a site password leaves the machine only for
its own site, an API key only for its service or a host a human has allowed,
and a redirect may not carry the secret anywhere else. Scrubbing of echoed
encodings is checked here too. It is not the boundary.

Written against the public tools, so the assertions fail while any public host
is still accepted and while encoded or reversed forms come back intact.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote, quote_plus

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pytest_httpx import HTTPXMock

from conftest import TEST_VEK
from mcp_server import api_client, tools

pytestmark = pytest.mark.asyncio

SITE_PASSWORD = "DUMMY-pw-7f3a9c-LEAKCHECK"
SITE_PASSWORD_B64 = "RFVNTVktcHctN2YzYTljLUxFQUtDSEVDSw=="
API_KEY = "DUMMY-key-7f3a9c-LEAKCHECK"
SLASHY_PASSWORD = "DUMMY-pw/7f3a9c+LEAKCHECK"
USERNAME = "leakuser"


def _encrypt(payload: dict) -> tuple[str, str]:
    iv = os.urandom(12)
    ciphertext = AESGCM(TEST_VEK).encrypt(iv, json.dumps(payload).encode(), None)
    return ciphertext.hex(), iv.hex()


def _list_api_keys(httpx_mock: HTTPXMock, *names: str) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{api_client.BASE_URL}/apikeys",
        json={"entries": [{"name": name} for name in names]},
    )


def _mock_api_key(
    httpx_mock: HTTPXMock,
    name: str,
    *,
    service: str,
    api_key: str,
    service_hint: str | None = None,
    copies: int = 1,
) -> None:
    blob, iv = _encrypt({"service": service, "api_key": api_key, "notes": ""})
    body = {"encrypted_blob": blob, "iv": iv}
    if service_hint is not None:
        body["service_hint"] = service_hint
    for _ in range(copies):
        _list_api_keys(httpx_mock, name)
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/apikeys/{name}",
            json=body,
        )


def _mock_vault(
    httpx_mock: HTTPXMock,
    site_name: str,
    *,
    username: str = USERNAME,
    password: str = SITE_PASSWORD,
    login_url: str | None = None,
    copies: int = 1,
) -> None:
    blob, iv = _encrypt({"username": username, "password": password, "notes": ""})
    body = {
        "encrypted_blob": blob,
        "iv": iv,
        "site_name": site_name,
    }
    if login_url is not None:
        body["login_url"] = login_url
    for _ in range(copies):
        _list_api_keys(httpx_mock)
        httpx_mock.add_response(
            method="GET",
            url=f"{api_client.BASE_URL}/vault/{site_name}",
            json=body,
        )


def _allow(entry: str, *hosts: str) -> None:
    path = Path.home() / ".psamvault" / "policy.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"entries": {entry: {"allow_hosts": list(hosts)}}}),
        encoding="utf-8",
    )


def _forms(secret: str, username: str | None = None) -> dict[str, str]:
    raw = secret.encode()
    std = base64.b64encode(raw).decode()
    url = base64.urlsafe_b64encode(raw).decode()
    forms = {
        "raw": secret,
        "std": std,
        "std_unpadded": std.rstrip("="),
        "url": url,
        "url_unpadded": url.rstrip("="),
        "quoted": quote(secret, safe=""),
        "quoted_plus": quote_plus(secret),
        "hex": raw.hex(),
        "HEX": raw.hex().upper(),
        "reversed": secret[::-1],
    }
    if username is not None:
        pair = f"{username}:{secret}".encode()
        basic = base64.b64encode(pair).decode()
        basic_url = base64.urlsafe_b64encode(pair).decode()
        forms["basic"] = basic
        forms["basic_unpadded"] = basic.rstrip("=")
        forms["basic_url"] = basic_url
        forms["basic_url_unpadded"] = basic_url.rstrip("=")
    return forms


async def test_site_password_to_foreign_host_is_refused(mock_tool_deps, httpx_mock: HTTPXMock):
    _mock_vault(httpx_mock, "leakcheck.example")

    result = await tools.use_credential(
        "leakcheck.example",
        "https://httpbin.org/anything",
        inject_as="bearer_token",
    )

    assert "error" in result
    assert "httpbin.org" in result["error"]
    assert "policy.json" in result["error"]
    assert "allow_hosts" in result["error"]
    assert SITE_PASSWORD not in json.dumps(result)


async def test_site_password_to_unrelated_echo_host_is_refused(mock_tool_deps, httpx_mock: HTTPXMock):
    _mock_vault(httpx_mock, "leakcheck.example")

    result = await tools.use_credential(
        "leakcheck.example",
        "https://postman-echo.com/get",
        inject_as="bearer_token",
    )

    assert "error" in result
    assert "postman-echo.com" in result["error"]
    assert "policy.json" in result["error"]
    assert "allow_hosts" in result["error"]
    assert SITE_PASSWORD not in json.dumps(result)


async def test_site_password_to_own_site_and_subdomain_is_allowed(
    mock_tool_deps, httpx_mock: HTTPXMock
):
    _mock_vault(httpx_mock, "leakcheck.example", copies=2)
    httpx_mock.add_response(
        method="GET",
        url="https://leakcheck.example/home",
        json={"ok": True},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.leakcheck.example/v1",
        json={"ok": True},
    )

    own = await tools.use_credential("leakcheck.example", "https://leakcheck.example/home")
    sub = await tools.use_credential("leakcheck.example", "https://api.leakcheck.example/v1")

    assert own["success"] is True
    assert own["data"] == {"ok": True}
    assert sub["success"] is True
    assert sub["data"] == {"ok": True}


async def test_site_password_does_not_cover_a_sibling_registrable_domain(
    mock_tool_deps, httpx_mock: HTTPXMock
):
    _mock_vault(httpx_mock, "leakcheck.example")

    result = await tools.use_credential(
        "leakcheck.example",
        "https://notleakcheck.example/steal",
    )

    assert "error" in result
    assert "notleakcheck.example" in result["error"]
    assert "policy.json" in result["error"]


async def test_multi_label_public_suffix_does_not_collapse(mock_tool_deps, httpx_mock: HTTPXMock):
    """bank.co.uk must not authorise attacker.co.uk. www.bank.co.uk stays allowed."""
    _mock_vault(httpx_mock, "bank.co.uk", copies=2)
    httpx_mock.add_response(
        method="GET",
        url="https://www.bank.co.uk/login",
        json={"ok": True},
    )

    own = await tools.use_credential("bank.co.uk", "https://www.bank.co.uk/login")
    foreign = await tools.use_credential("bank.co.uk", "https://attacker.co.uk/login")

    assert own["success"] is True
    assert "error" in foreign
    assert "attacker.co.uk" in foreign["error"]
    assert "policy.json" in foreign["error"]


async def test_private_suffix_does_not_cover_a_neighbour(mock_tool_deps, httpx_mock: HTTPXMock):
    """psam.github.io is one site. other.github.io is a different registrable domain."""
    _mock_vault(httpx_mock, "psam.github.io", copies=2)
    httpx_mock.add_response(
        method="GET",
        url="https://www.psam.github.io/app",
        json={"ok": True},
    )

    own = await tools.use_credential("psam.github.io", "https://www.psam.github.io/app")
    neighbour = await tools.use_credential("psam.github.io", "https://other.github.io/x")

    assert own["success"] is True
    assert "error" in neighbour
    assert "other.github.io" in neighbour["error"]
    assert "policy.json" in neighbour["error"]


async def test_login_url_binds_a_site_whose_name_is_not_a_host(
    mock_tool_deps, httpx_mock: HTTPXMock
):
    _mock_vault(
        httpx_mock,
        "MyBank",
        login_url="https://www.bank.co.uk/login",
        copies=2,
    )
    httpx_mock.add_response(
        method="GET",
        url="https://online.bank.co.uk/app",
        json={"ok": True},
    )

    allowed = await tools.use_credential("MyBank", "https://online.bank.co.uk/app")
    refused = await tools.use_credential("MyBank", "https://httpbin.org/get")

    assert allowed["success"] is True
    assert "error" in refused
    assert "httpbin.org" in refused["error"]
    assert "policy.json" in refused["error"]


async def test_api_key_to_service_host_is_allowed_and_foreign_host_is_refused(
    mock_tool_deps, httpx_mock: HTTPXMock
):
    _mock_api_key(httpx_mock, "gh-prod", service="GitHub API", api_key=API_KEY, copies=3)
    httpx_mock.add_response(
        method="GET",
        url="https://api.github.com/user",
        json={"login": "psam"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://uploads.github.com/v1",
        json={"ok": True},
    )

    service = await tools.use_credential("gh-prod", "https://api.github.com/user")
    sibling = await tools.use_credential("gh-prod", "https://uploads.github.com/v1")
    foreign = await tools.use_credential("gh-prod", "https://httpbin.org/anything")

    assert service["success"] is True
    assert service["data"]["login"] == "psam"
    assert sibling["success"] is True
    assert "error" in foreign
    assert "httpbin.org" in foreign["error"]
    assert "policy.json" in foreign["error"]
    assert API_KEY not in json.dumps(foreign)


async def test_api_key_service_hint_selects_the_verify_recipe_host(
    mock_tool_deps, httpx_mock: HTTPXMock
):
    _mock_api_key(
        httpx_mock,
        "search-key",
        service="unspecified",
        api_key=API_KEY,
        service_hint="tavily",
        copies=2,
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.tavily.com/usage",
        json={"ok": True},
    )

    allowed = await tools.use_credential("search-key", "https://api.tavily.com/usage")
    refused = await tools.use_credential("search-key", "https://httpbin.org/get")

    assert allowed["success"] is True
    assert "error" in refused
    assert "httpbin.org" in refused["error"]
    assert "policy.json" in refused["error"]


async def test_api_key_with_no_known_service_host_is_refused_until_a_human_allows_one(
    mock_tool_deps, httpx_mock: HTTPXMock
):
    _mock_api_key(httpx_mock, "ledger-key", service="Custom Ledger", api_key=API_KEY)

    refused = await tools.use_credential("ledger-key", "https://api.ledger.example/v1")

    assert "error" in refused
    assert "no known service host" in refused["error"]
    assert "api.ledger.example" in refused["error"]
    assert "policy.json" in refused["error"]
    assert "allow_hosts" in refused["error"]
    assert API_KEY not in json.dumps(refused)

    _allow("ledger-key", "api.ledger.example")
    httpx_mock.add_response(
        method="GET",
        url="https://api.ledger.example/v1",
        json={"ok": True},
    )
    # The vault lookup is not cached. Register it again for the second call.
    _mock_api_key(httpx_mock, "ledger-key", service="Custom Ledger", api_key=API_KEY)

    allowed = await tools.use_credential("ledger-key", "https://api.ledger.example/v1")

    assert allowed["success"] is True
    assert allowed["data"] == {"ok": True}


async def test_allow_hosts_does_not_override_the_ssrf_block(mock_tool_deps, httpx_mock: HTTPXMock):
    _allow("leakcheck.example", "127.0.0.1")
    _mock_vault(httpx_mock, "leakcheck.example")

    result = await tools.use_credential("leakcheck.example", "http://127.0.0.1/latest")

    assert "error" in result
    assert "loopback" in result["error"].lower() or "ssrf" in result["error"].lower()
    assert SITE_PASSWORD not in json.dumps(result)


async def test_redirect_to_a_foreign_host_is_not_followed(mock_tool_deps, httpx_mock: HTTPXMock):
    _mock_api_key(httpx_mock, "gh-prod", service="github", api_key=API_KEY)
    httpx_mock.add_response(
        method="GET",
        url="https://api.github.com/start",
        status_code=302,
        headers={"Location": "https://httpbin.org/get"},
    )

    result = await tools.use_credential("gh-prod", "https://api.github.com/start")

    assert "error" in result
    assert "httpbin.org" in result["error"]
    assert "redirect" in result["error"].lower()
    assert API_KEY not in json.dumps(result)
    requested = [str(request.url) for request in httpx_mock.get_requests()]
    assert not any("httpbin.org" in url for url in requested)


async def test_redirect_to_an_allowed_host_is_followed(mock_tool_deps, httpx_mock: HTTPXMock):
    _mock_api_key(httpx_mock, "gh-prod", service="github", api_key=API_KEY)
    httpx_mock.add_response(
        method="GET",
        url="https://api.github.com/old",
        status_code=302,
        headers={"Location": "https://uploads.github.com/new"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://uploads.github.com/new",
        json={"ok": True},
    )

    result = await tools.use_credential("gh-prod", "https://api.github.com/old")

    assert result["success"] is True
    assert result["data"] == {"ok": True}


async def test_redirect_to_a_loopback_host_stays_blocked(mock_tool_deps, httpx_mock: HTTPXMock):
    _mock_vault(httpx_mock, "leakcheck.example")
    httpx_mock.add_response(
        method="GET",
        url="https://leakcheck.example/go",
        status_code=302,
        headers={"Location": "http://127.0.0.1/latest"},
    )

    result = await tools.use_credential("leakcheck.example", "https://leakcheck.example/go")

    assert "error" in result
    assert "127.0.0.1" in result["error"]
    assert "loopback" in result["error"].lower() or "ssrf" in result["error"].lower()
    requested = [str(request.url) for request in httpx_mock.get_requests()]
    assert not any("127.0.0.1" in url for url in requested)


async def test_malformed_policy_does_not_grant_a_host(mock_tool_deps, httpx_mock: HTTPXMock):
    path = Path.home() / ".psamvault" / "policy.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{", encoding="utf-8")
    _mock_vault(httpx_mock, "leakcheck.example")

    result = await tools.use_credential("leakcheck.example", "https://httpbin.org/anything")

    assert "error" in result
    assert "httpbin.org" in result["error"]
    assert "policy.json" in result["error"]


async def test_echoed_secret_is_scrubbed_in_common_encodings(mock_tool_deps, httpx_mock: HTTPXMock):
    forms = _forms(SLASHY_PASSWORD, USERNAME)
    _mock_vault(httpx_mock, "leakcheck.example", password=SLASHY_PASSWORD)
    httpx_mock.add_response(
        method="GET",
        url="https://leakcheck.example/echo",
        headers={"X-Echo": forms["raw"], "X-Basic": forms["basic"]},
        json={"kept": "kept-marker", "echo": forms},
    )

    result = await tools.use_credential(
        "leakcheck.example",
        "https://leakcheck.example/echo",
        inject_as="basic_auth",
    )

    assert result["success"] is True
    assert result["data"]["kept"] == "kept-marker"
    rendered = json.dumps(result)
    for label, value in forms.items():
        assert value not in rendered, label
    assert "[REDACTED]" in rendered


async def test_run_with_credential_redacts_base64_and_reversed(
    mock_tool_deps, httpx_mock: HTTPXMock, tmp_path: Path
):
    assert base64.b64encode(SITE_PASSWORD.encode()).decode() == SITE_PASSWORD_B64
    _mock_api_key(httpx_mock, "leak-key", service="Custom Ledger", api_key=SITE_PASSWORD)
    forms = _forms(SITE_PASSWORD)
    script = tmp_path / "show_secret.py"
    script.write_text(
        "import os, base64, sys\n"
        "from urllib.parse import quote\n"
        "v = os.environ['PW']\n"
        "sys.stdout.write('RAW=' + v + '\\n')\n"
        "sys.stdout.write('B64=' + base64.b64encode(v.encode()).decode() + '\\n')\n"
        "sys.stdout.write('B64U=' + base64.urlsafe_b64encode(v.encode()).decode() + '\\n')\n"
        "sys.stdout.write('HEX=' + v.encode().hex() + '\\n')\n"
        "sys.stdout.write('REV=' + v[::-1] + '\\n')\n"
        "sys.stdout.write('URL=' + quote(v, safe='') + '\\n')\n",
        encoding="utf-8",
    )
    command = f'"{sys.executable}" "{script}"'

    result = await tools.run_with_credential(
        site_name="leak-key",
        command=command,
        inject_as="env",
        env_var_name="PW",
    )

    assert result["exit_code"] == 0, result
    stdout = result["stdout"]
    assert "[REDACTED]" in stdout
    for label, value in forms.items():
        assert value not in stdout, label
    assert SITE_PASSWORD_B64 not in stdout
    assert SITE_PASSWORD[::-1] not in stdout


async def test_command_url_to_a_foreign_host_is_refused(mock_tool_deps, httpx_mock: HTTPXMock):
    _mock_vault(httpx_mock, "leakcheck.example")

    result = await tools.run_with_credential(
        site_name="leakcheck.example",
        command="echo https://httpbin.org/get",
        inject_as="env",
        env_var_name="PW",
    )

    assert result["exit_code"] == -1
    assert "error" in result
    assert "httpbin.org" in result["error"]
    assert "policy.json" in result["error"]
    assert SITE_PASSWORD not in json.dumps(result)


async def test_command_url_on_the_sites_own_host_is_allowed(mock_tool_deps, httpx_mock: HTTPXMock):
    _mock_vault(httpx_mock, "leakcheck.example")

    result = await tools.run_with_credential(
        site_name="leakcheck.example",
        command="echo https://api.leakcheck.example/v1",
        inject_as="env",
        env_var_name="PW",
    )

    assert result["exit_code"] == 0, result
    assert "https://api.leakcheck.example/v1" in result["stdout"]
    assert SITE_PASSWORD not in result["stdout"]
