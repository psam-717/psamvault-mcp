#!/usr/bin/env python3
"""Two-way sync between the installed psamvault-mcp skill and this repo's docs/skills/ pages.

**This is a merge, not a generator.** Both sides are edit points:

* the **installed skill** (``HERMES_HOME/skills/psam-custom/psamvault-mcp/SKILL.md``) is the copy an agent
  actually loads, and it is edited directly on the machine that runs it;
* the **docs pages** (``docs/skills/*.md``) are the public copy — the repo and the documentation site — and
  contributors edit those.

So each sync is a three-way merge, using the same engine ``git merge`` uses:

    base   = the marked sections as they were in the repo commit the installed copy last synced with
    mine   = the local side of this operation (installed for --pull, docs for --publish)
    theirs = the other side

A **section** is a marker-delimited block:

    <!-- section:<page>/<slug> -->
    ...text...
    <!-- /section:<page>/<slug> -->

``page`` is one of the docs pages (``install``, ``upgrade``, ``usage``); the block therefore already says
which file it belongs to. The heading line stays **outside** the markers, so a docs page may present a
section differently ("Step 0 — preconditions") from the installed skill ("Prerequisites") without that
being a difference. Front matter and cross-links also stay outside, so they are repo-only furniture and are
never touched by either direction.

Rules that keep it honest:

* only sections marked on **both** sides are synced — a section marked on one side is reported, never moved;
* a file with an unclosed marker, a duplicate section id, or a ``page`` that has no matching docs file is an
  error, and nothing is written;
* a conflict is **never auto-resolved**: the tool writes nothing and prints both versions for the human.

Commands:

    --check     (default) report differences. Exit 1 when the two sides are not in step.
    --init      record the baseline: the repo commit the installed copy is in step with.
    --pull      a contributor's docs edit  ->  written into the installed skill.
    --publish   an installed-skill edit    ->  written into the docs pages (then hand it to git).

State lives beside the installed skill (``.sync-state.json``), so the skill file itself stays clean.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PAGES = ("install", "upgrade", "usage")
DOCS_DIR = ("docs", "skills")
STATE_NAME = ".sync-state.json"

SECTION_RE = re.compile(
    r"<!--\s*section:(?P<id>[A-Za-z0-9][A-Za-z0-9._/-]*)\s*-->\r?\n"
    r"(?P<body>.*?)"
    r"<!--\s*/section:(?P=id)\s*-->\r?\n?",
    re.DOTALL,
)
ANY_MARKER_RE = re.compile(r"<!--\s*/?section:[^>]*-->")


class SyncError(RuntimeError):
    """A condition that must stop the tool rather than be worked around."""


# ── sections ──────────────────────────────────────────────────────────────────────────────────────


def read_sections(text: str, where: str) -> dict[str, str]:
    """id -> body for every marked section, validated."""
    found: dict[str, str] = {}
    for match in SECTION_RE.finditer(text):
        section_id = match.group("id")
        page = section_id.split("/", 1)[0]
        if "/" not in section_id or page not in PAGES:
            raise SyncError(
                f"{where}: section id {section_id!r} must be '<page>/<slug>' with page in {PAGES}"
            )
        if section_id in found:
            raise SyncError(f"{where}: section {section_id!r} appears twice")
        found[section_id] = match.group("body")
    # A marker that the regex above did not consume means the pair is malformed or unclosed.
    stray = ANY_MARKER_RE.findall(text)
    if len(stray) != 2 * len(found):
        raise SyncError(
            f"{where}: {len(stray)} section marker(s) for {len(found)} matched pair(s) — "
            "a marker is unclosed, nested, or its open/close ids do not match"
        )
    return found


def splice(text: str, updates: dict[str, str]) -> str:
    """Replace the bodies of the given sections in ``text``; everything else is byte-preserved."""
    nl = "\r\n" if "\r\n" in text else "\n"

    def replace(match: re.Match[str]) -> str:
        section_id = match.group("id")
        if section_id not in updates:
            return match.group(0)
        body = updates[section_id].replace("\r\n", "\n")
        if not body.endswith("\n"):
            body += "\n"
        body = body.replace("\n", nl)
        return f"<!-- section:{section_id} -->{nl}{body}<!-- /section:{section_id} -->{nl}"

    return SECTION_RE.sub(replace, text)


def load_docs(repo: Path) -> tuple[dict[str, str], dict[str, Path]]:
    sections: dict[str, str] = {}
    origin: dict[str, Path] = {}
    for page in PAGES:
        path = repo.joinpath(*DOCS_DIR, f"{page}.md")
        if not path.is_file():
            raise SyncError(f"missing docs page: {path}")
        for section_id, body in read_sections(path.read_text(encoding="utf-8"), str(path)).items():
            if section_id.split("/", 1)[0] != page:
                raise SyncError(
                    f"{path}: section {section_id!r} claims page {section_id.split('/')[0]!r}, "
                    f"but it lives in {page}.md"
                )
            sections[section_id] = body
            origin[section_id] = path
    return sections, origin


def load_installed(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise SyncError(f"installed skill not found: {path}")
    return read_sections(path.read_text(encoding="utf-8"), str(path))


def base_from_git(repo: Path, commit: str) -> dict[str, str]:
    """The docs sections as of ``commit`` — the last state both sides agreed on."""
    base: dict[str, str] = {}
    for page in PAGES:
        rel = "/".join((*DOCS_DIR, f"{page}.md"))
        proc = subprocess.run(
            ["git", "-C", str(repo), "show", f"{commit}:{rel}"],
            capture_output=True, text=True, encoding="utf-8",
        )
        if proc.returncode != 0:
            raise SyncError(
                f"cannot read {rel} at {commit} (is the commit still in this clone?): "
                f"{(proc.stderr or proc.stdout).strip()[:200]}"
            )
        base.update(read_sections(proc.stdout, f"{rel}@{commit}"))
    return base


# ── the merge ─────────────────────────────────────────────────────────────────────────────────────


def merge3(mine: str, base: str | None, theirs: str) -> tuple[bool, str]:
    """Three-way merge of one section. ``base is None`` means the section is new since the last sync."""
    if mine == theirs:
        return True, mine
    if base is None:
        # No common ancestor: identical is fine, different is an add/add conflict.
        return False, mine
    with tempfile.TemporaryDirectory(prefix="skill-merge-") as tmp:
        d = Path(tmp)
        for name, text in (("mine", mine), ("base", base), ("theirs", theirs)):
            (d / f"{name}.md").write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        proc = subprocess.run(
            ["git", "merge-file", "-p", "--marker-size=7", "mine.md", "base.md", "theirs.md"],
            cwd=d, capture_output=True, text=True, encoding="utf-8",
        )
    clean = proc.returncode == 0
    return clean, proc.stdout


# ── state ────────────────────────────────────────────────────────────────────────────────────────


def state_path(installed: Path) -> Path:
    return installed.parent / STATE_NAME


def load_state(installed: Path) -> dict:
    path = state_path(installed)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(installed: Path, state: dict) -> None:
    state_path(installed).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def head_commit(repo: Path) -> str:
    proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise SyncError(f"{repo} is not a git checkout: {proc.stderr.strip()[:200]}")
    return proc.stdout.strip()


# ── commands ─────────────────────────────────────────────────────────────────────────────────────


def plan(repo: Path, installed: Path, state: dict) -> tuple[dict, dict, dict, list[str], list[str]]:
    """Everything the commands need, with the reporting the human needs."""
    docs, _ = load_docs(repo)
    local = load_installed(installed)
    shared = sorted(set(docs) & set(local))
    docs_only = sorted(set(docs) - set(local))
    installed_only = sorted(set(local) - set(docs))
    base: dict[str, str] = {}
    if state.get("commit"):
        base = {k: v for k, v in base_from_git(repo, state["commit"]).items() if k in shared}
    return docs, local, base, docs_only, installed_only, shared


def cmd_check(repo: Path, installed: Path, state: dict) -> int:
    docs, local, base, docs_only, installed_only, shared = plan(repo, installed, state)
    print(f"=== sync check: {installed}")
    print(f"  shared sections : {len(shared)}")
    print(f"  docs-only       : {len(docs_only)}" + (f" — {', '.join(docs_only)}" if docs_only else ""))
    print(f"  installed-only  : {len(installed_only)}" + (f" — {', '.join(installed_only)}" if installed_only else ""))
    if not state.get("commit"):
        print("  baseline        : none — run --init to record the commit both sides are in step with")
    else:
        print(f"  baseline        : {state['commit']} ({state.get('repo', '?')})")

    problems: list[str] = []
    differing: list[str] = []
    merged_same = 0
    for section_id in shared:
        if not state.get("commit"):
            # No baseline yet: nothing can be merged, so only report whether the two sides agree.
            if local[section_id].strip() == docs[section_id].strip():
                merged_same += 1
            else:
                differing.append(section_id)
            continue
        clean, merged = merge3(mine=local[section_id], base=base.get(section_id), theirs=docs[section_id])
        if local[section_id] == docs[section_id]:
            merged_same += 1
        elif clean and merged.strip() == local[section_id].strip():
            print(f"  · {section_id}: installed side has changes to publish")
        elif clean and merged.strip() == docs[section_id].strip():
            print(f"  · {section_id}: docs side has changes to pull")
        elif clean:
            print(f"  · {section_id}: both sides changed — merges cleanly")
        else:
            problems.append(section_id)
            print(f"  ✗ {section_id}: CONFLICT (both sides edited the same lines)")
    print(f"  identical       : {merged_same}/{len(shared)} shared sections")
    if differing:
        print(f"  differing       : {len(differing)}/{len(shared)} — {', '.join(differing)}")
        print("  (no baseline yet: --init records it, then --pull/--publish can merge)")
        return 1
    if problems:
        print(f"\n  {len(problems)} conflict(s) — nothing is written until they are resolved by hand.")
        return 1
    if docs_only or installed_only:
        print("  local to one side: "
              f"docs-only {len(docs_only)}, installed-only {len(installed_only)}"
              + (f" — {', '.join(docs_only + installed_only)}" if docs_only or installed_only else ""))
    pending = len(shared) - merged_same
    if pending:
        print(f"\n  {pending} section(s) not in step — run --pull (docs -> installed) "
              "or --publish (installed -> docs)")
        return 1
    print("\n  ✓ in step")
    return 0


def cmd_init(repo: Path, installed: Path) -> int:
    docs, local, _, docs_only, installed_only, shared = plan(repo, installed, {})
    commit = head_commit(repo)
    # The baseline has to be readable from git, so an uncommitted edit to a docs page can never become
    # "the state both sides agreed on".
    at_head = base_from_git(repo, commit)
    missing = sorted(set(docs) - set(at_head))
    if missing:
        raise SyncError(
            f"{len(missing)} marked section(s) are not present in {commit} — commit the docs pages first: "
            + ", ".join(missing)
        )
    differing = [s for s in shared if docs[s].strip() != local[s].strip()]
    print(f"=== init: baseline -> {commit}")
    print(f"  shared sections : {len(shared)}")
    print(f"  already identical: {len(shared) - len(differing)}")
    if differing:
        print(f"  differ at baseline: {len(differing)} — {', '.join(differing)}")
        print("  (recorded as-is: the next --pull/--publish merges from here)")
    save_state(installed, {"repo": "psam-717/psamvault-mcp", "commit": commit,
                           "synced_utc": _utc()})
    print(f"  state written   : {state_path(installed)}")
    return 0


def _utc() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_sync(direction: str, repo: Path, installed: Path, state: dict, dry_run: bool,
              commit: bool = False, push: bool = False) -> int:
    docs, local, base, docs_only, installed_only, shared = plan(repo, installed, state)
    if not state.get("commit"):
        raise SyncError("no baseline recorded — run --init first")
    mine_is_installed = direction == "pull"
    mine = local if mine_is_installed else docs
    theirs = docs if mine_is_installed else local
    target = installed if mine_is_installed else None

    merged: dict[str, str] = {}
    conflicts: list[str] = []
    changed: list[str] = []
    for section_id in shared:
        clean, text = merge3(mine=mine[section_id], base=base.get(section_id), theirs=theirs[section_id])
        if not clean:
            conflicts.append(section_id)
            continue
        if text.strip() != mine[section_id].strip():
            changed.append(section_id)
        merged[section_id] = text

    verb = "into the installed skill" if mine_is_installed else "into the docs pages"
    print(f"=== sync {direction} {verb}{' (dry run)' if dry_run else ''}")
    print(f"  shared sections : {len(shared)}   changed: {len(changed)}"
          + (f" — {', '.join(changed)}" if changed else ""))
    if conflicts:
        print(f"\n  ✗ {len(conflicts)} conflict(s) — NOTHING written:")
        for section_id in conflicts:
            print(f"\n  ── {section_id} ── mine ─\n{_indent(mine[section_id])}")
            print(f"  ── {section_id} ── theirs ─\n{_indent(theirs[section_id])}")
        return 1
    if not changed:
        print("  already in step — nothing to write")
        return 0
    if dry_run:
        print("\n  dry run: no files written")
        return 0

    if mine_is_installed:
        assert target is not None
        original = target.read_text(encoding="utf-8")
        target.write_text(splice(original, merged), encoding="utf-8")
        print(f"  written         : {target}")
        if docs_only:
            print(f"  note            : docs-only sections were not touched: {', '.join(docs_only)}")
        _advance_baseline(repo, installed, state)
        return 0

    for section_id in changed:
        page = section_id.split("/", 1)[0]
        path = repo.joinpath(*DOCS_DIR, f"{page}.md")
        path.write_text(splice(path.read_text(encoding="utf-8"), {section_id: merged[section_id]}),
                        encoding="utf-8")
        print(f"  written         : {path}  ({section_id})")
    if commit:
        _commit_docs(repo, push)
    else:
        print("  next            : review the diff, then --commit [--push], or do it by hand")
    _advance_baseline(repo, installed, state)
    return 0


def _docs_dirty(repo: Path) -> bool:
    proc = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--", "docs/skills"],
                          capture_output=True, text=True)
    return bool(proc.stdout.strip())


def _advance_baseline(repo: Path, installed: Path, state: dict) -> None:
    """Move the baseline only to a commit that actually contains the current docs content.

    Advancing while docs/skills is dirty would record a base nobody ever had, and the next merge would
    read that difference as an edit.
    """
    if _docs_dirty(repo):
        print("  baseline        : NOT advanced — docs/skills has uncommitted changes; commit, then --init")
        return
    commit = head_commit(repo)
    save_state(installed, {**state, "commit": commit, "synced_utc": _utc()})
    print(f"  baseline        : advanced to {commit}")


def _commit_docs(repo: Path, push: bool) -> None:
    subprocess.run(["git", "-C", str(repo), "add", "docs/skills"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m",
                    "docs(skills): sync the shared sections from the installed skill"], check=True)
    print(f"  committed       : {head_commit(repo)}")
    if push:
        subprocess.run(["git", "-C", str(repo), "push"], check=True)
        print(f"  pushed          : {_branch(repo)}")


def _branch(repo: Path) -> str:
    proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
                          capture_output=True, text=True)
    return proc.stdout.strip()


def _indent(text: str) -> str:
    return "\n".join("    " + line for line in text.splitlines())


def find_installed() -> Path:
    home = os.environ.get("HERMES_HOME")
    candidates = []
    if home:
        candidates.append(Path(home) / "skills" / "psam-custom" / "psamvault-mcp" / "SKILL.md")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "hermes" / "skills" / "psam-custom" / "psamvault-mcp" / "SKILL.md")
    candidates.append(Path.home() / ".hermes" / "skills" / "psam-custom" / "psamvault-mcp" / "SKILL.md")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SyncError("cannot find the installed skill — pass --installed <path>")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Two-way merge between the installed skill and docs/skills/.")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--installed", type=Path, help="the installed SKILL.md (default: discovered)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="report differences (default)")
    group.add_argument("--init", action="store_true", help="record the baseline commit")
    group.add_argument("--pull", action="store_true", help="docs -> installed")
    group.add_argument("--publish", action="store_true", help="installed -> docs")
    parser.add_argument("--dry-run", action="store_true", help="with --pull/--publish: show, write nothing")
    parser.add_argument("--commit", action="store_true", help="with --publish: commit the docs changes")
    parser.add_argument("--push", action="store_true", help="with --publish --commit: push the branch")
    args = parser.parse_args(argv)

    try:
        installed = args.installed or find_installed()
        state = load_state(installed)
        if args.init:
            return cmd_init(args.repo, installed)
        if args.pull or args.publish:
            return _run_sync("pull" if args.pull else "publish", args.repo, installed, state,
                             args.dry_run, commit=args.commit, push=args.push)
        return cmd_check(args.repo, installed, state)
    except SyncError as exc:
        print(f"sync-skill-docs: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
