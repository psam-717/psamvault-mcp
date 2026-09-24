"""Regression test: handle_call_tool must forward the parameters each tool declares.

`scan_and_protect` declared `project_name` in its schema and `tools.scan_and_protect` honoured it, but the
dispatch branch in main.py forwarded only `project_dir` and `patterns`. The result was a documented feature
that silently did nothing: keys were stored as `env/.env/KEY` instead of `project_name/.env/KEY`, and
`list_api_keys(project_name=...)` then found nothing under that project.

This test fails the moment a declared parameter stops being passed through.
"""

import os as _os
import sys as _sys
from unittest.mock import AsyncMock, patch

import pytest

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from mcp_server import main as server  # noqa: E402


def _spy():
    return AsyncMock(return_value={"scanned_dir": "/tmp/proj", "secrets_found": 0})


class TestScanAndProtectForwarding:
    @pytest.mark.asyncio
    async def test_project_name_reaches_the_handler(self):
        spy = _spy()
        with patch.object(server, "is_logged_in", return_value=True), \
                patch.object(server.tools, "scan_and_protect", new=spy):
            await server.handle_call_tool("scan_and_protect", {
                "project_dir": "/tmp/proj",
                "patterns": ["MY_CUSTOM_KEY"],
                "project_name": "acme",
            })
        spy.assert_awaited_once_with(
            project_dir="/tmp/proj",
            patterns=["MY_CUSTOM_KEY"],
            project_name="acme",
        )

    @pytest.mark.asyncio
    async def test_omitted_project_name_stays_none(self):
        """Absent means the backwards-compatible `env/.env/KEY` layout, not a crash."""
        spy = _spy()
        with patch.object(server, "is_logged_in", return_value=True), \
                patch.object(server.tools, "scan_and_protect", new=spy):
            await server.handle_call_tool("scan_and_protect", {"project_dir": "/tmp/proj"})
        spy.assert_awaited_once_with(
            project_dir="/tmp/proj",
            patterns=None,
            project_name=None,
        )

    @pytest.mark.asyncio
    async def test_every_declared_property_is_forwarded(self):
        """Guards the class, not just this parameter: nothing declared may be silently dropped."""
        declared = set()
        for tool in server.TOOL_DEFINITIONS:
            if tool.name == "scan_and_protect":
                declared = set(tool.inputSchema.get("properties", {}))
        assert declared, "scan_and_protect not found in TOOL_DEFINITIONS"
        spy = _spy()
        payload = {key: ("x" if key != "patterns" else ["X"]) for key in declared}
        with patch.object(server, "is_logged_in", return_value=True), \
                patch.object(server.tools, "scan_and_protect", new=spy):
            await server.handle_call_tool("scan_and_protect", payload)
        forwarded = set(spy.await_args.kwargs)
        assert declared <= forwarded, f"declared but not forwarded: {sorted(declared - forwarded)}"
