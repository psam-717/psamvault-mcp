"""Real stdio: the audit ``ask`` bit matches whether elicitation/create was sent.

The client is the MCP Python SDK over stdio, not a desktop UI. A client that
does not pass an elicitation callback advertises no elicitation capability, so
the server must not send a form and must record ``ask`` false. A callback that
cancels or declines proves the request was on the wire, so ``ask`` is true.
A yes is allow, and with no vault session the tool stops at the login sentence.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.shared.context import RequestContext

_POLICY = {
    "mcp_use": "enforce",
    "entries": {
        "ledger-key": {
            "allow_tools": ["use_credential"],
            "ask_each_time": True,
        }
    },
}

ElicitCallback = Callable[
    [RequestContext[ClientSession, object], types.ElicitRequestParams],
    Awaitable[types.ElicitResult | types.ErrorData],
]


def _server_env(home: Path) -> dict[str, str]:
    """A disposable home and a null keyring. No live bus, no real vault."""
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["PYTHONPATH"] = ""
    env["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
    env["PSAMVAULT_API_URL"] = "https://127.0.0.1:9"
    env["PSAMVAULT_MCP_SKIP_UPDATE_CHECK"] = "1"
    env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/does-not-exist/psamvault-consent-bus"
    env["XDG_RUNTIME_DIR"] = str(home / "xdg")
    for name in ("PSAMVAULT_VEK", "PSAMVAULT_TOKEN", "PSAMVAULT_PEPPER"):
        env.pop(name, None)
    return env


async def _call(tmp_path: Path, callback: ElicitCallback | None):
    home = tmp_path / "home"
    vault = home / ".psamvault"
    vault.mkdir(parents=True)
    (home / "xdg").mkdir()
    (vault / "policy.json").write_text(json.dumps(_POLICY), encoding="utf-8")
    sent = {"n": 0}

    async def counting(ctx, params):
        sent["n"] += 1
        assert callback is not None
        return await callback(ctx, params)

    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from mcp_server.main import main; main()"],
        env=_server_env(home),
    )
    async with stdio_client(params) as (read, write):
        kwargs = {"elicitation_callback": counting} if callback is not None else {}
        async with ClientSession(read, write, **kwargs) as session:
            await session.initialize()
            result = await session.call_tool(
                "use_credential",
                {
                    "site_name": "ledger-key",
                    "target_url": "https://api.ledger.example/v1",
                },
            )
    lines = (vault / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    text = result.content[0].text
    return json.loads(lines[0]), text, sent["n"]


@pytest.mark.asyncio
async def test_stdio_unsupported_client_records_ask_false(tmp_path):
    row, text, sent = await _call(tmp_path, None)
    assert sent == 0
    assert '"error": "STOP.' in text
    assert (row["decision"], row["reason"], row["ask"]) == ("deny", "ask-unanswered", False)


@pytest.mark.asyncio
async def test_stdio_cancel_records_ask_true(tmp_path):
    async def cancel(ctx, params):
        return types.ElicitResult(action="cancel")

    row, text, sent = await _call(tmp_path, cancel)
    assert sent == 1
    assert '"error": "STOP.' in text
    assert (row["decision"], row["reason"], row["ask"]) == ("deny", "ask-unanswered", True)


@pytest.mark.asyncio
async def test_stdio_decline_records_ask_true(tmp_path):
    async def decline(ctx, params):
        return types.ElicitResult(action="decline")

    row, text, sent = await _call(tmp_path, decline)
    assert sent == 1
    assert "said no" in text
    assert (row["decision"], row["reason"], row["ask"]) == ("deny", "ask-declined", True)


@pytest.mark.asyncio
async def test_stdio_yes_stops_at_no_session(tmp_path):
    async def allow(ctx, params):
        return types.ElicitResult(action="accept", content={"allow": True})

    row, text, sent = await _call(tmp_path, allow)
    assert sent == 1
    assert "Not logged in" in text
    assert "STOP." not in text
    assert (row["decision"], row["reason"], row["ask"]) == ("allow", "listed", True)
