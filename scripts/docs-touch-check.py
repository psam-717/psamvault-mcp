#!/usr/bin/env python3
"""Docs-with-the-change gate: did THIS change bring its own documentation?

``scripts/check-docs-surface.py`` answers the absolute question — "do the docs describe software that
exists?". This gate answers the relative one: "is the change I am about to merge reflected in the
docs?". A PR that adds a command, a flag, a tool or an environment variable and never touches a page
is exactly the failure this catches: every existing page stays true, so every other gate stays green,
and the docs quietly fall behind the code.

Verdicts, against the change between two refs (default ``origin/main...HEAD``, plus uncommitted work):

  FAIL  a surface item ADDED by the change is named nowhere in the docs. A new command, flag or tool
        is user-visible by definition, so its page is part of the change.
        CLI: every registered command path and long flag the app gained.
        MCP: every tool name ``TOOL_DEFINITIONS`` gained.
  FAIL  a surface item REMOVED by the change is still named in the docs — the page now describes
        something that no longer exists.
  FAIL  an environment variable ADDED by the change is absent from the reference docs.
  WARN  user-facing code changed and NO documentation file did. The page that describes the behaviour
        is probably the page that is now wrong. Warned, not failed: the change may genuinely be
        invisible to users (internal refactor, performance work, a rename nobody sees).

The warning is waived by an explicit decision, never by silence:

    python scripts/docs-touch-check.py --allow-no-docs "pure refactor, no user-visible behaviour"
    python scripts/docs-touch-check.py --allow-no-docs "test-only"

In CI the same flag is passed when the PR carries the ``docs-not-needed`` label, so the waiver is a
human assertion on the record (the label shows in the PR list and the reason is printed in the log).
The waiver covers the whole gate — the label *is* the decision.

Scope note: parameters inside a tool's input schema are not diffed (they are not reliably named in the
definition text). A param change still trips the WARN, and ``check-docs-surface.py`` keeps the written
surface honest.

Usage:
    python scripts/docs-touch-check.py [repo] [--base origin/main] [--head HEAD]
    python scripts/docs-touch-check.py --committed-only          # CI: the checkout has no local edits
    python scripts/docs-touch-check.py --mode cli|mcp            # default: detected from the layout

Exit codes: 0 clean (warnings allowed) · 1 a defect · 2 usage or setup error (never a silent pass).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# ── what counts as what ────────────────────────────────────────────────────────
# Documentation a user reads. CHANGELOG files are deliberately absent: a release ledger is not a page.
DOC_PATTERNS = (
    "docs/",
    "README.md",
    "SKILL.md",
    "AGENTS.md",
    "PLAYWRIGHT_INTEGRATION.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
)

# Where the package's own code lives (the packages are flat, so root .py files count).
CODE_DIRS = ("command/", "mcp_server/", "cli/", "server/")
CODE_ROOT_SUFFIX = ".py"

# Everything that is neither code nor documentation (tests, CI, the changelog ledger, scaffolding) is
# reported as "neither" rather than dropped: a tests-only change must say so out loud instead of looking
# like an empty comparison. The ledger has its own workflow (changelog-unreleased-workflow).

# Pure noise from an editable install or a local build: never worth reporting.
NOISE_PREFIXES = (".venv/", "dist/", "node_modules/", ".pytest_cache/")
NOISE_PARTS = (".egg-info", "__pycache__")

# Documentation surfaces that describe the code surface, per mode.
DOC_EXTRA = {
    "cli": (),
    "mcp": ("SKILL.md", "AGENTS.md", "PLAYWRIGHT_INTEGRATION.md"),
}

ENV_VAR = re.compile(r"\bPSAMVAULT_[A-Z0-9_]+\b")
MCP_TOOL_NAME = re.compile(r'name="([a-z][a-z0-9_]*)"')
TOOL_DOC_BLOCK = "TOOL_DEFINITIONS = ["

# Run inside a checkout to print its CLI surface as JSON. Self-contained on purpose: it must work
# against an OLD base ref, whose copy of this repo may not have any of today's helpers.
CLI_SURFACE_PROBE = r'''
import json, sys
sys.path.insert(0, ".")
from typer.main import get_command
import main
app = get_command(main.app)
paths, options = [], {"--help", "--version"}
stack = [("", app)]
while stack:
    prefix, cmd = stack.pop()
    for name, sub in (getattr(cmd, "commands", None) or {}).items():
        full = f"{prefix}{name}"
        paths.append(full)
        stack.append((f"{full} ", sub))
    for param in getattr(cmd, "params", []):
        options.update(getattr(param, "opts", []) + getattr(param, "secondary_opts", []))
print(json.dumps({"commands": sorted(set(paths)), "flags": sorted(options)}))
'''


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout


def _normalize(path: str) -> str:
    return path.replace("\\", "/").strip()


def _is_noise(path: str) -> bool:
    return (
        any(path.startswith(prefix) for prefix in NOISE_PREFIXES)
        or any(part in path for part in NOISE_PARTS)
        or path.endswith(".pyc")
    )


def changed_files(repo: Path, base: str, head: str, committed_only: bool) -> tuple[list[str], str]:
    """Committed diff (``base...head``) plus uncommitted work, so this is usable mid-edit."""
    committed = _git(repo, "diff", "--name-only", "--no-renames", f"{base}...{head}").split()
    files = [_normalize(p) for p in committed]
    how = f"{base}...{head}"
    if not committed_only:
        for line in _git(repo, "status", "--porcelain").splitlines():
            if len(line) > 3:
                files.append(_normalize(line[3:]))
        how += " + working tree"
    seen: dict[str, None] = {}
    for path in files:
        if path and not _is_noise(path):
            seen.setdefault(path, None)
    return list(seen), how


def is_doc(path: str, mode: str) -> bool:
    if any(path.startswith(p) if p.endswith("/") else path == p for p in DOC_PATTERNS):
        return True
    return path in DOC_EXTRA.get(mode, ())


def is_code(path: str, mode: str) -> bool:
    if is_doc(path, mode):
        return False
    if any(path.startswith(d) for d in CODE_DIRS):
        return True
    # Flat package: a root-level module is the product; the noise above is already filtered out.
    return "/" not in path and path.endswith(CODE_ROOT_SUFFIX)


def doc_corpus(root: Path, mode: str) -> str:
    """Every page that may describe the surface, as one LF-normalised string."""
    files = sorted((root / "docs").rglob("*.md")) if (root / "docs").is_dir() else []
    for extra in ("README.md", *DOC_EXTRA.get(mode, ())):
        candidate = root / extra
        if candidate.exists():
            files.append(candidate)
    parts = []
    for page in files:
        try:
            parts.append(page.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n"))
        except OSError:
            continue
    return "\n".join(parts)


def names_a_command(corpus: str, path: str) -> bool:
    """True when an invocation of ``path`` appears, in either its grouped or flat spelling."""
    for form in {path, path.replace(" ", "-")}:
        if re.search(rf"(?<![\w-])(?:psamvault|pv)\s+{re.escape(form)}(?![\w-])", corpus):
            return True
    return False


def names_token(corpus: str, token: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", corpus) is not None


def _worktree(repo: Path, ref: str, tmp: Path) -> Path:
    """A checkout of ``ref`` in a scratch directory. Raises RuntimeError when the ref is unusable."""
    target = tmp / "base"
    _git(repo, "worktree", "add", "--detach", "--force", str(target), ref)
    return target


def cli_surface_at(root: Path) -> dict[str, list[str]]:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as fh:
        fh.write(CLI_SURFACE_PROBE)
        probe = fh.name
    try:
        proc = subprocess.run(
            [sys.executable, probe], cwd=str(root), capture_output=True, text=True
        )
    finally:
        Path(probe).unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"cannot read the CLI surface in {root}: {(proc.stderr or proc.stdout).strip()[:400]}"
        )
    return json.loads(proc.stdout)


def mcp_tools_at(root: Path) -> set[str]:
    source = (root / "mcp_server" / "main.py").read_text(encoding="utf-8", errors="replace")
    if TOOL_DOC_BLOCK not in source:
        raise RuntimeError(f"{root}: TOOL_DEFINITIONS not found in mcp_server/main.py")
    start = source.index(TOOL_DOC_BLOCK)
    end = source.index("\n]", start)
    return set(MCP_TOOL_NAME.findall(source[start:end]))


def env_vars_at(root: Path, mode: str) -> set[str]:
    """Environment variables the product reads — the strings a user must be able to look up."""
    dirs = [root] + [root / d.rstrip("/") for d in CODE_DIRS]
    found: set[str] = set()
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            if path.name == "setup.py":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            found.update(ENV_VAR.findall(text))
    return found


def surface_at(root: Path, mode: str) -> dict[str, set[str]]:
    if mode == "cli":
        cli = cli_surface_at(root)
        return {
            "command": set(cli["commands"]),
            "flag": set(cli["flags"]),
            "env": env_vars_at(root, mode),
        }
    return {"tool": mcp_tools_at(root), "env": env_vars_at(root, mode)}


def describe(kind: str, name: str) -> str:
    if kind == "command":
        return f"command `psamvault {name}`"
    if kind == "flag":
        return f"flag `{name}`"
    if kind == "tool":
        return f"tool `{name}`"
    return f"environment variable `{name}`"


def documented(corpus: str, kind: str, name: str, mode: str) -> bool:
    if kind == "command":
        return names_a_command(corpus, name)
    if kind == "tool":
        return names_token(corpus, name)
    return names_token(corpus, name)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Fail when a change adds or removes surface with no docs change.")
    parser.add_argument("repo", nargs="?", default=".", help="repo path (default: current directory)")
    parser.add_argument("--base", default="origin/main", help="base ref (default: origin/main)")
    parser.add_argument("--head", default="HEAD", help="head ref (default: HEAD)")
    parser.add_argument("--mode", choices=["cli", "mcp"], help="default: detected from the layout")
    parser.add_argument("--committed-only", action="store_true", help="ignore uncommitted edits (CI)")
    parser.add_argument("--allow-no-docs", metavar="REASON", help="record why this change needs no docs")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    if not (repo / ".git").exists():
        print(f"not a git repo: {repo}", file=sys.stderr)
        return 2
    mode = args.mode
    if mode is None:
        if (repo / "mcp_server" / "main.py").exists():
            mode = "mcp"
        elif (repo / "main.py").exists() and (repo / "command").is_dir():
            mode = "cli"
        else:
            print(f"cannot tell whether {repo} is the CLI or the MCP repo — pass --mode", file=sys.stderr)
            return 2

    print(f"=== docs-with-the-change gate ({mode} mode, {repo.name}) ===")
    try:
        files, how = changed_files(repo, args.base, args.head, args.committed_only)
    except RuntimeError as exc:
        print(f"  ✗ cannot compare: {exc}", file=sys.stderr)
        print("  (in CI: check out with fetch-depth: 0 so the base ref exists)", file=sys.stderr)
        return 2
    print(f"  compared : {how}")
    if not files:
        print("  · nothing differs from the base — nothing to check (already merged, or wrong base?)")
        return 0

    docs_changed = [f for f in files if is_doc(f, mode)]
    code_changed = [f for f in files if is_code(f, mode)]
    neither = [f for f in files if f not in docs_changed and f not in code_changed]
    print(f"  code     : {len(code_changed)} file(s)" + (f" — {', '.join(code_changed[:5])}" if code_changed else ""))
    print(f"  docs     : {len(docs_changed)} file(s)" + (f" — {', '.join(docs_changed[:5])}" if docs_changed else ""))
    if neither:
        print(f"  neither  : {len(neither)} file(s) (tests, CI, changelog, scaffolding)")

    if not code_changed:
        print("  ✓ no user-facing code changed — nothing to document")
        return 0

    with tempfile.TemporaryDirectory(prefix="docs-touch-") as tmp:
        tmp_path = Path(tmp)
        base_root = None
        try:
            base_root = _worktree(repo, args.base, tmp_path)
            base = surface_at(base_root, mode)
            head = surface_at(repo, mode)
        except RuntimeError as exc:
            print(f"  ✗ cannot read the base surface: {exc}", file=sys.stderr)
            return 2
        finally:
            if base_root is not None:
                _git(repo, "worktree", "remove", "--force", str(base_root), check=False)

        corpus = doc_corpus(repo, mode)
        problems: list[str] = []
        added_report: list[str] = []
        for kind in sorted(base):
            added = sorted(head[kind] - base[kind])
            removed = sorted(base[kind] - head[kind])
            for name in added:
                if documented(corpus, kind, name, mode):
                    added_report.append(f"documented: {describe(kind, name)}")
                else:
                    problems.append(f"ADDED but not documented anywhere: {describe(kind, name)}")
            for name in removed:
                if documented(corpus, kind, name, mode):
                    problems.append(f"REMOVED but the docs still name it: {describe(kind, name)}")

    for line in added_report:
        print(f"  · {line}")

    if args.allow_no_docs:
        print(f"  ✓ waived by decision — {args.allow_no_docs}")
        for problem in problems:
            print(f"    (still worth knowing) {problem}")
        return 0

    if problems:
        print(f"\n  ✗ {len(problems)} problem(s) — the docs do not match this change:")
        for problem in problems:
            print(f"    - {problem}")
        print("\n  Update the page that describes it, in THIS PR:")
        print("    docs/reference/commands.md       a command or flag (CLI)")
        print("    docs/reference/tools.md          a tool, parameter or returned field (MCP)")
        print("    docs/reference/configuration.md  an environment variable, config key or host wiring")
        print("    docs/installation.md             install / upgrade / repair behaviour")
        print("    docs/guides/*.md                 a workflow a user performs")
        print("    docs/features.md                 what a capability is for")
        print("    README.md / SKILL.md / AGENTS.md the surfaces a host or agent reads")
        return 1

    if docs_changed:
        print("  ✓ the docs move with the code")
        return 0

    print(
        "::warning title=Docs not touched::user-facing code changed with no documentation change "
        "— is the page that describes this behaviour still right? "
        "Pure refactor or test-only? Record the decision with --allow-no-docs \"why\", "
        "or label the PR `docs-not-needed`."
    )
    print("  ! warning: user-facing code changed and no docs file did (not a failure)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
