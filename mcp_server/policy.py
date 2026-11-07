"""MCP-use policy loaded from ``~/.psamvault/policy.json``.

This is the read path that matches psamvault-cli ``policy.load`` and
``policy.mcp_plan`` for ``mcp_use``, ``allow_tools`` and ``ask_each_time``.
The server does not import the CLI and does not write the file.

A missing, unreadable, or non-object file is ``mcp_use`` ``audit`` with no
entries. That is not ``enforce``. Destination hosts are a different read of
the same file (``mcp_server.egress``): a broken file still allows no extra host.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from mcp_server import mcp_use

_GROUP_OR_WORLD_BITS = stat.S_IRWXG | stat.S_IRWXO


def policy_path() -> Path:
    """The CLI policy file. Resolved at call time so tests can redirect home."""
    return Path.home() / ".psamvault" / "policy.json"


@dataclass
class Policy:
    """Effective MCP-use values. The file itself is never rewritten."""

    mcp_use: str = mcp_use.DEFAULT_MODE
    mcp_use_implicit: bool = True
    entry_tools: dict[str, list[str]] = field(default_factory=dict)
    entry_ask: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    source: str = "default"


def load(path: Path | None = None) -> Policy:
    """Load MCP-use fields. Never raises. A bad file is audit and no entries."""
    target = Path(path) if path is not None else policy_path()
    loaded = Policy()
    try:
        if not target.exists():
            return loaded
        raw = target.read_text(encoding="utf-8")
    except Exception as exc:
        # Same as the CLI: a file that cannot be read, including one that is
        # not UTF-8, is the implicit audit default. It is not enforce.
        loaded.warnings.append(f"could not read {target}: {exc}")
        return loaded

    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        loaded.warnings.append(f"{target} is not valid JSON ({exc.msg}) — using safe defaults")
        return loaded

    if not isinstance(data, dict):
        loaded.warnings.append(f"{target} must contain a JSON object — using safe defaults")
        return loaded

    loaded.source = "file"
    _load_mcp_use(loaded, data)
    _load_entries(loaded, data)

    if os.name == "posix":
        try:
            mode_bits = target.stat().st_mode
            if mode_bits & _GROUP_OR_WORLD_BITS:
                loaded.warnings.append(f"{target} is readable by other users — run: chmod 600 {target}")
        except OSError:
            pass
    return loaded


def _load_mcp_use(loaded: Policy, data: dict) -> None:
    """Absent or JSON null is audit, and implicit. A bad value is audit too."""
    if "mcp_use" not in data or data.get("mcp_use") is None:
        loaded.mcp_use_implicit = True
        return
    mode = data.get("mcp_use")
    if isinstance(mode, str) and mode in mcp_use.MODES:
        loaded.mcp_use = mode
        loaded.mcp_use_implicit = False
        return
    loaded.mcp_use_implicit = False
    loaded.warnings.append(f"unknown mcp_use {mode!r} — using {mcp_use.DEFAULT_MODE}")


def _load_entries(loaded: Policy, data: dict) -> None:
    if "entries" not in data or data.get("entries") is None:
        return
    entries = data.get("entries")
    if not isinstance(entries, dict):
        loaded.warnings.append("entries must be an object — ignored")
        return
    for name, spec in entries.items():
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(spec, dict):
            loaded.warnings.append(f"entries[{name!r}] must be an object — ignored")
            continue
        _load_entry_tools(loaded, name, spec)


def _load_entry_tools(loaded: Policy, name: str, spec: dict) -> None:
    """Fill tool and ask maps. Does not rewrite the file."""
    if "allow_tools" in spec:
        tools = spec.get("allow_tools")
        if tools is None:
            loaded.entry_tools[name] = []
        elif not isinstance(tools, list):
            loaded.warnings.append(f"entries[{name!r}].allow_tools must be a list — ignored")
            loaded.entry_tools[name] = []
        else:
            accepted: list[str] = []
            for item in tools:
                verdict = mcp_use.judge(str(item), strict=False)
                if not verdict.normalized:
                    detail = verdict.problem or f"{item!r} is not a tool name"
                    loaded.warnings.append(f"entries[{name!r}].allow_tools: {detail}")
                    continue
                if verdict.problem:
                    loaded.warnings.append(f"entries[{name!r}].allow_tools: {verdict.problem}")
                if verdict.normalized not in accepted:
                    accepted.append(verdict.normalized)
            loaded.entry_tools[name] = accepted

    if "ask_each_time" not in spec or spec.get("ask_each_time") is None:
        return
    flag = spec.get("ask_each_time")
    if isinstance(flag, bool):
        loaded.entry_ask[name] = flag
        return
    loaded.warnings.append(
        f"entries[{name!r}].ask_each_time must be true or false — treated as false"
    )
    loaded.entry_ask[name] = False


def mcp_use_notice(loaded: Policy) -> str | None:
    """The planned-flip sentence when ``mcp_use`` was not in the file."""
    return mcp_use.absent_default_notice(implicit=loaded.mcp_use_implicit)


def mcp_plan(loaded: Policy, entry: str, tool: str) -> mcp_use.McpPlan:
    """The decision for one use of one entry, before elicitation.

    Every policy key whose trimmed name casefolds to the argument or its leaf
    contributes. Tool lists are unioned. ``ask`` is true only under
    ``enforce`` when a match has ``ask_each_time`` and the use is allowed.
    """
    wanted = _entry_names(entry)
    tools: list[str] = []
    flag = False
    for name, listed in loaded.entry_tools.items():
        if name.strip().casefold() in wanted:
            for item in listed:
                if item not in tools:
                    tools.append(item)
    for name, enabled in loaded.entry_ask.items():
        if name.strip().casefold() in wanted and enabled:
            flag = True
    decision, reason = mcp_use.decide(loaded.mcp_use, tools, tool)
    ask = mcp_use.should_ask(loaded.mcp_use, decision, ask_each_time=flag)
    return mcp_use.McpPlan(
        decision=decision,
        reason=reason,
        ask=ask,
        mcp_use=loaded.mcp_use,
        notice=mcp_use_notice(loaded),
    )


def _entry_names(entry: str) -> set[str]:
    """The argument and the leaf after the last ``/``, casefolded and trimmed."""
    text = (entry or "").strip()
    if not text:
        return set()
    names = {text.casefold()}
    leaf = text.rsplit("/", 1)[-1].strip()
    if leaf:
        names.add(leaf.casefold())
    return names


def mode_status(path: Path | None = None) -> dict:
    """What ``get_version`` reports. The notice is not added to tool results."""
    loaded = load(path)
    return {
        "mode": loaded.mcp_use,
        "notice": mcp_use_notice(loaded),
        "warnings": list(loaded.warnings),
    }
