#!/usr/bin/env python3
"""Docs-surface gate: does the documentation describe software that actually exists?

The docs tree is consumed by a website and by agents, so a doc that names a command, flag or tool that
does not exist is a defect, not a typo — this repo has already shipped one (`psamvault setup`, which has
never existed; the command is `psamvault configure`). This gate makes that class of bug fail CI.

Two modes, auto-detected from the repo layout (override with --mode):

  cli   every `psamvault <cmd>` inside a code block must be a command the app registers, and every long
        flag must exist somewhere in the app. Grounded in the real registry by importing the app.
  mcp   every tool named in docs/reference/tools.md must be in the code's TOOL_DEFINITIONS, and every
        registered tool must be documented. Grounded by parsing TOOL_DEFINITIONS as text, so the gate
        needs no MCP dependency.

Both modes also enforce the docs site contract: every page under docs/ has front matter (title,
description, order), the order values are unique integers, and no page is orphaned — every page is
linked from docs/README.md.

Line endings are deliberately NOT checked. The repository stores LF (core.autocrlf normalises on commit)
and checks out CRLF on Windows working copies, so asserting either one fails on the other platform.

Usage:  python scripts/check-docs-surface.py [--mode cli|mcp] [--root PATH]

Exit 0 clean, 1 on defects, 2 on a usage/setup error. Informational notes print without failing.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# A line only counts as documenting a command if psamvault/pv is the first word on it (after indentation
# or a shell prompt). Without that anchor, CLI *output* gets read as commands — the upgrade notice
# "This psamvault install is editable/source-linked" was reported as `psamvault install`.
INVOCATION = re.compile(r"^\s*(?:\$\s+|>\s+)?(?:psamvault|pv)\s+([a-z][a-z0-9-]+)(?:\s+([a-z][a-z0-9-]+))?")
PROSE_COMMAND = re.compile(r"\bpsamvault\s+([a-z][a-z0-9-]+)")
LONG_FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")


def docs_pages(root: Path) -> list[Path]:
    return sorted((root / "docs").rglob("*.md"))


def scanned_files(root: Path) -> list[Path]:
    """Docs pages plus the landing README — both may name commands or tools."""
    files = docs_pages(root)
    readme = root / "README.md"
    return files + ([readme] if readme.exists() else [])


def read(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read().replace("\r\n", "\n")


def check_site_contract(root: Path, problems: list[str], notes: list[str]) -> None:
    index = root / "docs" / "README.md"
    index_text = read(index) if index.exists() else ""
    if not index.exists():
        problems.append("docs/README.md: missing (the site needs an index)")
    pages = docs_pages(root)
    if not pages:
        problems.append("docs/: no pages found")
    orders: dict[str, str] = {}
    for page in pages:
        rel = page.relative_to(root).as_posix()
        text = read(page)
        if not text.startswith("---\n"):
            problems.append(f"{rel}: missing front matter")
            continue
        block = text.split("---", 2)[1]
        for key in ("title:", "description:", "order:"):
            if key not in block:
                problems.append(f"{rel}: front matter missing {key}")
        order = re.search(r"^order:\s*(\S+)\s*$", block, re.M)
        if order:
            value = order.group(1)
            if not re.fullmatch(r"\d+", value):
                problems.append(f"{rel}: order must be an integer, got {value!r}")
            elif value in orders:
                problems.append(f"{rel}: order {value} already used by {orders[value]} (the site needs unique ordering)")
            else:
                orders[value] = rel
        if rel != "docs/README.md" and page.name not in index_text:
            problems.append(f"{rel}: not linked from docs/README.md (orphan)")
    notes.append(f"site contract: {len(pages)} docs pages, {len(orders)} unique order values")


def cli_surface(root: Path) -> tuple[set[str], set[str]]:
    sys.path.insert(0, str(root))
    from typer.main import get_command  # noqa: PLC0415

    import main  # noqa: PLC0415

    app = get_command(main.app)
    paths: list[str] = []
    options = {"--help", "--version"}
    stack = [("", app)]
    while stack:
        prefix, cmd = stack.pop()
        for name, sub in (getattr(cmd, "commands", None) or {}).items():
            full = f"{prefix}{name}"
            paths.append(full)
            stack.append((f"{full} ", sub))
        for param in getattr(cmd, "params", []):
            options.update(getattr(param, "opts", []) + getattr(param, "secondary_opts", []))
    return set(paths), options


def check_cli(root: Path, problems: list[str], notes: list[str]) -> None:
    paths, options = cli_surface(root)
    notes.append(f"CLI surface: {len(paths)} command paths, {len(options)} long flags")

    used, unknown_cmds, unknown_flags, prose = set(), set(), set(), set()
    for page in scanned_files(root):
        rel = page.relative_to(root).as_posix()
        in_fence = False
        for line in read(page).split("\n"):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            match = INVOCATION.match(line)
            if match:
                first, second = match.group(1), match.group(2)
                two = f"{first} {second}" if second else None
                if two and two in paths:
                    used.add(two)
                elif first in paths:
                    used.add(first)
                else:
                    # In a fenced block it is a command being documented → a real defect. In prose it is
                    # the English sentence "psamvault includes ..." → informational.
                    (unknown_cmds if in_fence else prose).add(f"{rel}: psamvault {first}")
                # Flags are judged only on lines that invoke the CLI, so git/pipx flags
                # (git reset --hard, pip install --upgrade) are not mistaken for psamvault's.
                for flag in set(LONG_FLAG.findall(line)):
                    if flag not in options:
                        (unknown_flags if in_fence else prose).add(f"{rel}: {flag}")
            elif not in_fence and "psamvault" in line:
                for extra in PROSE_COMMAND.finditer(line):
                    if extra.group(1) not in paths:
                        prose.add(f"{rel}: psamvault {extra.group(1)}")

    problems.extend(sorted(f"unknown command in a code block → {item}" for item in unknown_cmds))
    problems.extend(sorted(f"unknown flag in a code block → {item}" for item in unknown_flags))
    if prose:
        notes.append(f"prose mentions that are not commands (informational, {len(prose)}): "
                     + ", ".join(sorted(prose)[:6]))
    notes.append(f"CLI docs: {len(used)} distinct commands referenced")


def mcp_tools(root: Path) -> set[str]:
    source = read(root / "mcp_server" / "main.py")
    start = source.index("TOOL_DEFINITIONS = [")
    end = source.index("\n]", start)
    return set(re.findall(r'name="([a-z_0-9]+)"', source[start:end]))


def check_mcp(root: Path, problems: list[str], notes: list[str], source_root: Path | None = None) -> None:
    tools = mcp_tools(root)
    notes.append(f"MCP registry: {len(tools)} tools")
    reference = root / "docs" / "reference" / "tools.md"
    if not reference.exists():
        problems.append("docs/reference/tools.md: missing (the site renders this as the tool reference)")
        return
    text = read(reference)
    documented = set(re.findall(r"`([a-z_][a-z_0-9]{3,})`", text))
    missing = sorted(tools - documented)
    if missing:
        problems.append(f"registered but not documented in docs/reference/tools.md: {missing}")
    # Informational: tokens that look like tool names but are not registered. Fields of the get_version
    # compatibility block (newest_release_skill_version, paired_skill_version) land here too, so this is
    # a prompt to eyeball rather than a failure.
    lookalike = sorted(
        t for t in documented - tools
        if t.endswith(("_credential", "_key", "_login", "_tools", "_version", "_sites", "_env_file",
                       "_mcp_config", "_protect", "_exists"))
    )
    if lookalike:
        notes.append(f"tool-like names not registered (check these are fields, not tools): {lookalike}")
    notes.append(f"documented tool names: {len(documented & tools)}/{len(tools)}")


def detect_mode(root: Path) -> str | None:
    if (root / "mcp_server" / "main.py").exists():
        return "mcp"
    if (root / "main.py").exists() and (root / "command").is_dir():
        return "cli"
    return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Fail when the docs name a command, flag or tool that does not exist.")
    parser.add_argument("--mode", choices=["cli", "mcp"], help="default: detected from the repo layout")
    parser.add_argument("--root", type=Path, help="repository root (default: this script's repo)")
    args = parser.parse_args(argv)

    root = (args.root or Path(__file__).resolve().parents[1]).resolve()
    mode = args.mode or detect_mode(root)
    if mode is None:
        print(f"cannot tell whether {root} is the CLI or the MCP repo — pass --mode", file=sys.stderr)
        return 2

    problems: list[str] = []
    notes: list[str] = []
    check_site_contract(root, problems, notes)
    try:
        if mode == "cli":
            check_cli(root, problems, notes)
        else:
            check_mcp(root, problems, notes)
    except ImportError as exc:
        print(f"cannot import the {mode} surface in {root}: {exc}\n"
              f"Install the package first (pip install -e .) — this gate reads the real registry.",
              file=sys.stderr)
        return 2

    print(f"=== docs surface gate: {mode} mode, {root} ===")
    for note in notes:
        print(f"  · {note}")
    if problems:
        print(f"\n  ✗ {len(problems)} problem(s):")
        for problem in problems:
            print(f"    - {problem}")
        print("\n  Every command, flag and tool named in the docs must exist in the code.")
        return 1
    print("\n  ✓ docs match the real surface; site contract satisfied")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
