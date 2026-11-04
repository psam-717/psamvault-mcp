"""One JSONL row per MCP use of a tool that decrypts a secret.

This is psamvault-mcp's copy of psamvault-cli ``audit.record_mcp_use``. The
server does not import the CLI. A write failure is swallowed: auditing must
not fail the tool.

On-disk shape, one compact JSON object per line, UTF-8, file mode ``0600``,
directory mode ``0700``, in ``~/.psamvault/audit.jsonl``. At 1,000,000 bytes
the current file is renamed to ``audit.jsonl.1`` (an existing ``.1`` is
replaced; there is no ``.2``).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from mcp_server import mcp_use as mcp_policy

AUDIT_FILENAME = "audit.jsonl"
MAX_AUDIT_BYTES = 1_000_000


def audit_path() -> Path:
    """The CLI audit trail. Resolved at call time so tests can redirect home."""
    return Path.home() / ".psamvault" / AUDIT_FILENAME


def _rotate(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size >= MAX_AUDIT_BYTES:
            rotated = path.parent / (path.name + ".1")
            if rotated.exists():
                rotated.unlink()
            path.rename(rotated)
    except OSError:
        pass


def record_mcp_use(
    *,
    tool: str,
    entry: str | None,
    decision: str,
    reason: str,
    mcp_use: str,
    ask: bool = False,
    path: Path | None = None,
) -> None:
    """Append one MCP-use row. Never raises.

    The row is dropped when ``tool`` is not a tool name, ``reason`` is not in
    :data:`mcp_use.REASONS`, ``decision`` is not ``allow`` or ``deny``, or
    ``mcp_use`` is not a known mode. That is what keeps a secret out of the trail.
    """
    normalized = mcp_policy.normalize_tool(tool) if isinstance(tool, str) else None
    if (
        not normalized
        or decision not in (mcp_policy.ALLOW, mcp_policy.DENY)
        or reason not in mcp_policy.REASONS
        or mcp_use not in mcp_policy.MODES
    ):
        return
    target = Path(path) if path is not None else audit_path()
    row = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": f"mcp {normalized}",
        "entry": entry,
        "caller": "mcp",
        "signals": [],
        "tty": False,
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "decision": decision,
        "token_id": None,
        "policy": None,
        "tool": normalized,
        "reason": reason,
        "mcp_use": mcp_use,
        "ask": bool(ask),
    }
    try:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(target.parent, 0o700)
        _rotate(target)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        os.chmod(target, 0o600)
    except OSError:
        pass


def read(path: Path | None = None) -> list[dict]:
    """Read the trail back. A missing or unreadable file is an empty list."""
    target = Path(path) if path is not None else audit_path()
    rows: list[dict] = []
    try:
        with open(target, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows
