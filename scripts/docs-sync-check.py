"""Doc-drift check for a release: do the docs still describe the code that is about to ship?

Run this FIRST in any release/publish flow (see the `psamvault-release` and `py-publish` skills). Docs
are not a follow-up step: a release with stale docs is an unfinished release, and the next agent reads
those docs as truth. This script makes the rule mechanical instead of aspirational.

It checks, against the code's actual tool surface (`mcp_server.main.TOOL_DEFINITIONS`):
  1. every tool the code exposes is documented in README / AGENTS / SKILL;
  2. no doc table still names a tool the code no longer has (the failure a tool COUNT cannot catch);
  3. every "N tools" claim in the docs matches the real count;
  4. `mcp_server/compatibility.json`'s newest entry lists exactly the code's tools.

Exit 0 when the docs and the code agree, 1 otherwise (with each problem printed).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Surfaces that must name every tool.
TOOL_DOCS = ("README.md", "AGENTS.md", "SKILL.md")
# Everything scanned for stale names / counts.
ALL_DOCS = TOOL_DOCS + (
    "CLAUDE.md",
    "NEMOCLAW_COMPAT.md",
    "mcp_server/agent_guide.py",
    "mcp_server/prompts/general-rules.md",
    "docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md",
)

FIRST_CELL = re.compile(r"^\|\s*`([a-z][a-z0-9_]*)`", re.MULTILINE)
COUNT_CLAIM = re.compile(r"\b(\d{1,3})\s+tools\b")
TOOL_SHAPED = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$")
# snake_case first-column entries that are legitimately not tools
NOT_TOOLS = {"psam_vault_backend", "hermes_gateway", "mcp_servers"}


def code_tools() -> list[str]:
    from mcp_server.main import TOOL_DEFINITIONS

    return sorted(tool.name for tool in TOOL_DEFINITIONS)


def problems(repo: Path = REPO, tools: list[str] | None = None) -> list[str]:
    tools = sorted(tools if tools is not None else code_tools())
    found: list[str] = []
    for rel in ALL_DOCS:
        path = repo / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in FIRST_CELL.findall(text):
            if name in tools or name in NOT_TOOLS:
                continue
            if TOOL_SHAPED.match(name):
                found.append(f"{rel}: names '{name}', which the code no longer exposes")
        for count in COUNT_CLAIM.findall(text):
            if int(count) != len(tools):
                found.append(f"{rel}: claims {count} tools, the code exposes {len(tools)}")
        if rel in TOOL_DOCS:
            for name in tools:
                if f"`{name}`" not in text:
                    found.append(f"{rel}: does not document the tool '{name}'")
    contract = repo / "mcp_server" / "compatibility.json"
    if contract.is_file():
        releases = json.loads(contract.read_text(encoding="utf-8"))["releases"]
        newest = max(releases, key=lambda rel: rel["mcp"])
        if sorted(newest["tools"]) != tools:
            found.append(
                f"compatibility.json: newest entry ({newest['mcp']}) lists a different tool surface "
                "than the code"
            )
    return found


def main() -> int:
    tools = code_tools()
    found = problems(tools=tools)
    if found:
        print(f"docs are OUT OF SYNC with the code ({len(tools)} tools):")
        for line in found:
            print(f"  - {line}")
        print("\nUpdate the docs before releasing (README, AGENTS, SKILL, agent prompts, docs/).")
        return 1
    print(f"docs in sync with the code ({len(tools)} tools)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
