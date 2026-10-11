"""Doc-drift check for a release: do the docs still describe the code that is about to ship?

Run this FIRST in any release/publish flow (see the `psamvault-release` and `py-publish` skills). Docs
are not a follow-up step: a release with stale docs is an unfinished release, and the next agent reads
those docs as truth. This script makes the rule mechanical instead of aspirational.

It checks, against the code's actual tool surface (`mcp_server.main.TOOL_DEFINITIONS`):
  1. every tool the code exposes is documented in README / AGENTS / the tool reference;
  2. no doc table still names a tool the code no longer has (the failure a tool COUNT cannot catch);
  3. every "N tools" claim in the docs matches the real count;
  4. `mcp_server/compatibility.json`'s newest entry lists exactly the code's tools;
  5. the unreleased-work file exists (unreleased changes must be tracked somewhere central);
  6. `CHANGELOG.md`'s newest section is the release the contract calls newest, so a release that
     forgets to roll the unreleased entries into the changelog cannot ship quietly.

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

# Surfaces that must name every tool. The repository-root SKILL.md is deliberately NOT one of them: it is
# an index into docs/skills/, while the catalogue of tools lives in the tool reference. It is still scanned
# below for stale names and counts.
TOOL_DOCS = ("README.md", "AGENTS.md", "docs/reference/tools.md")
# Everything scanned for stale tool names / "N tools" claims. The tool reference is deliberately NOT in this
# list: its tables are parameter tables, so their first cells are parameter names (`site_name`), not tools.
# The other direction — a tool removed by a change while the docs still name it — is enforced by
# docs-touch-check, which compares against the change that removes it.
ALL_DOCS = (
    "README.md",
    "AGENTS.md",
    "SKILL.md",
    "CLAUDE.md",
    "NEMOCLAW_COMPAT.md",
    "mcp_server/agent_guide.py",
    "mcp_server/prompts/general-rules.md",
    "docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md",
)
# Directories whose every page is scanned too, so a new skill page cannot escape the check.
DOC_DIRS = ("docs/skills",)

FIRST_CELL = re.compile(r"^\|\s*`([a-z][a-z0-9_]*)`", re.MULTILINE)
COUNT_CLAIM = re.compile(r"\b(\d{1,3})\s+tools\b")
TOOL_SHAPED = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$")
RELEASED_HEADING = re.compile(r"^##\s*\[?(\d+\.\d+\.\d+)\]?", re.MULTILINE)
# snake_case first-column entries that are legitimately not tools
NOT_TOOLS = {"psam_vault_backend", "hermes_gateway", "mcp_servers"}
CHANGELOG = "CHANGELOG.md"
UNRELEASED = "CHANGELOG.unreleased.md"


def code_tools() -> list[str]:
    from mcp_server.main import TOOL_DEFINITIONS

    return sorted(tool.name for tool in TOOL_DEFINITIONS)


def problems(repo: Path = REPO, tools: list[str] | None = None) -> list[str]:
    tools = sorted(tools if tools is not None else code_tools())
    found: list[str] = []
    scanned = list(ALL_DOCS)
    for directory in DOC_DIRS:
        scanned += [
            path.relative_to(repo).as_posix()
            for path in sorted((repo / directory).glob("*.md"))
        ]
    for rel in dict.fromkeys(scanned):
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
    contract_version: str | None = None
    if contract.is_file():
        releases = json.loads(contract.read_text(encoding="utf-8"))["releases"]
        newest = max(releases, key=lambda rel: rel["mcp"])
        contract_version = newest["mcp"]
        if sorted(newest["tools"]) != tools:
            found.append(
                f"compatibility.json: newest entry ({newest['mcp']}) lists a different tool surface "
                "than the code"
            )

    if not (repo / UNRELEASED).is_file():
        found.append(
            f"{UNRELEASED} is missing — merged-but-unpublished work must be tracked there "
            "(it is what the next release and the changelog are built from)"
        )
    changelog = repo / CHANGELOG
    if not changelog.is_file():
        found.append(f"{CHANGELOG} is missing — released history must live there")
    else:
        versions = RELEASED_HEADING.findall(changelog.read_text(encoding="utf-8", errors="replace"))
        if not versions:
            found.append(f"{CHANGELOG}: no '## <version>' section found")
        elif contract_version and versions[0] != contract_version:
            found.append(
                f"{CHANGELOG}: newest section is {versions[0]}, but the contract's newest release is "
                f"{contract_version} — roll the unreleased entries in at release time"
            )
    return found


def main() -> int:
    tools = code_tools()
    found = problems(tools=tools)
    if found:
        print(f"docs are OUT OF SYNC with the code ({len(tools)} tools):")
        for line in found:
            print(f"  - {line}")
        print("\nUpdate the docs before releasing (README, AGENTS, the tool reference, agent prompts, docs/, CHANGELOG.md).")
        return 1
    print(f"docs in sync with the code ({len(tools)} tools)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
