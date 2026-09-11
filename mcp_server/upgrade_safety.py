"""Upgrade safety for `psamvault-compat --apply` — the psamvault-cli lesson, applied to the MCP.

An install that replaces the running server can break in ways the install itself cannot see:

1. a **repo-sourced** install runs whatever is in the working tree, so pulling over a dirty tree either
   fails ("local changes would be overwritten") or clobbers uncommitted work;
2. a **half-finished** install (dependency resolution, a broken wheel) leaves the venv with no working
   server, and nothing puts the previous version back.

Mirrors `psamvault-cli/upgrade_utils.py`: detect state → snapshot → stash → pull --ff-only → restore
(park the stash on conflict) → install → smoke test → roll back on failure. Local work is never lost
and a failed upgrade is never left installed.
"""

from __future__ import annotations

import datetime
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

STASH_PREFIX = "psamvault-mcp-upgrade-autostash"
KEEP_BACKUPS = 5
SMOKE_CODE = (
    "import json, mcp_server;"
    "from mcp_server.main import TOOL_DEFINITIONS;"
    "print(json.dumps({'version': mcp_server.__version__,"
    " 'tools': sorted(t.name for t in TOOL_DEFINITIONS)}))"
)


def _git(repo: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=timeout
    )


def _utc_stamp() -> str:
    # Microseconds so back-to-back runs get unique, sortable names.
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S-%f")


# ── git state ──────────────────────────────────────────────────────────────────
def git_repo_state(repo: Path, upstream: str = "origin/main", fetch: bool = False) -> dict:
    """dirty / ahead / behind / branch for a repo, relative to ``upstream``.

    ``ok`` is False when the upstream ref is unknown (no remote, never fetched), in which case
    ahead/behind are meaningless rather than zero.

    ``fetch=True`` refreshes the remote-tracking ref first. Without it, ahead/behind are measured
    against the LAST fetch — a clone that is genuinely behind can report "0 behind", so any report
    shown to a user should fetch (or say it didn't).
    """
    inside = _git(repo, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0:
        return {"ok": False, "dirty": None, "ahead": None, "behind": None, "branch": None,
                "head": None, "fetched": False, "reason": f"not a git repository: {repo}"}

    fetched = False
    if fetch and "/" in upstream:
        fetched = _git(repo, "fetch", "--quiet", upstream.split("/", 1)[0], timeout=180).returncode == 0

    dirty = bool(_git(repo, "status", "--porcelain").stdout.strip())
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or None
    head = _git(repo, "rev-parse", "--short", "HEAD").stdout.strip() or None

    def _count(rev_range: str) -> int | None:
        result = _git(repo, "rev-list", "--count", rev_range)
        if result.returncode != 0:
            return None
        try:
            return int(result.stdout.strip() or "0")
        except ValueError:
            return None

    behind, ahead = _count(f"HEAD..{upstream}"), _count(f"{upstream}..HEAD")
    return {
        "ok": behind is not None and ahead is not None,
        "dirty": dirty,
        "ahead": ahead or 0,
        "behind": behind or 0,
        "branch": branch,
        "head": head,
        "fetched": fetched,
        "reason": None if behind is not None else f"{upstream} is not a known ref (never fetched?)",
    }


def render_repo_state(state: dict) -> str:
    if not state.get("ok"):
        return f"repo state unknown — {state.get('reason')}"
    bits = [f"branch {state['branch']}", f"HEAD {state['head']}"]
    bits.append("dirty (uncommitted changes)" if state["dirty"] else "clean")
    position = f"{state['ahead']} ahead / {state['behind']} behind origin/main"
    if not state.get("fetched"):
        position += " (as of the last fetch)"
    bits.append(position)
    return ", ".join(bits)


# ── stash → pull → restore ─────────────────────────────────────────────────────
def stash_and_pull(repo: Path, remote: str = "origin", branch: str = "main") -> dict:
    """Bring a repo-sourced install up to date WITHOUT risking the user's uncommitted work.

    - dirty tree → stash (including untracked) under a labelled, recoverable name
    - ``git pull --ff-only <remote> <branch>``
    - pull failed → restore the stash immediately (the upgrade stops, nothing is lost)
    - restore conflicts → the stash is LEFT PARKED and the label is printed, so nothing is dropped

    Returns ``{"ok", "stashed", "conflict", "pulled", "message"}``.
    """
    label = f"{STASH_PREFIX}-{_utc_stamp()}"
    stashed = False
    state = git_repo_state(repo)

    if state.get("dirty"):
        stash = _git(repo, "stash", "push", "-u", "-m", label)
        if stash.returncode != 0:
            return {"ok": False, "stashed": False, "conflict": False, "pulled": False,
                    "message": f"could not stash local changes:\n{stash.stderr.strip()}"}
        stashed = True

    pull = _git(repo, "pull", "--ff-only", remote, branch, timeout=300)
    if pull.returncode != 0:
        restored = False
        if stashed:
            restored = _git(repo, "stash", "pop").returncode == 0
        tail = "your local changes were restored." if restored else (
            f"your local changes are still stashed as '{label}' (restore with: git stash pop)"
            if stashed else "no local changes were involved."
        )
        return {"ok": False, "stashed": stashed, "conflict": False, "pulled": False,
                "message": f"git pull --ff-only {remote} {branch} failed — {tail}\n{pull.stderr.strip()}"}

    if stashed:
        pop = _git(repo, "stash", "pop")
        if pop.returncode != 0:
            # A conflicting pop leaves the stash in place: nothing is dropped.
            return {"ok": True, "stashed": True, "conflict": True, "pulled": True,
                    "message": ("upgrade pulled cleanly but restoring your local changes conflicted. "
                                f"Your work is parked in stash '{label}'. Resolve it with:\n"
                                "  git stash list\n  git stash pop   # after resolving conflicts")}

    return {"ok": True, "stashed": stashed, "conflict": False, "pulled": True, "message": ""}


# ── source-install detection ───────────────────────────────────────────────────
def is_pipx_editable(package: str = "psamvault-mcp") -> bool:
    """True when the pipx venv is an editable/source install.

    Installing a released wheel over an editable install silently detaches the repo link — worth
    telling the user before it happens, since ``--from-git`` is the source-install track.
    """
    try:
        result = subprocess.run(["pipx", "list", "--json"], capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return False
        meta = (json.loads(result.stdout or "{}").get("venvs", {}).get(package, {})
                .get("metadata", {}))
        main_pkg = meta.get("main_package", {}) or {}
        return bool(main_pkg.get("editable")) or str(main_pkg.get("package_or_url", "")).startswith(
            ("file://", "git+file://")
        )
    except Exception:
        return False


# ── snapshot ───────────────────────────────────────────────────────────────────
def snapshot_skill(skill_path: Path, backups_dir: Path | None = None, keep: int = KEEP_BACKUPS) -> Path | None:
    """Copy the installed skill aside before ``--apply`` overwrites it. Prunes to ``keep``."""
    if not skill_path.is_file():
        return None
    parent = backups_dir or skill_path.parent / "backups"
    parent.mkdir(parents=True, exist_ok=True)
    backup = _unique_dir(parent, "backup")
    shutil.copy2(skill_path, backup / skill_path.name)
    for old in sorted(parent.glob("backup-*"))[:-keep]:
        shutil.rmtree(old, ignore_errors=True)
    return backup


def _unique_dir(parent: Path, prefix: str) -> Path:
    """A fresh timestamped directory.

    Windows' clock granularity means two rapid calls can produce the SAME stamp string, which would
    silently collapse separate backups into one directory (and lose the later one). Disambiguate.
    """
    base = parent / f"{prefix}-{_utc_stamp()}"
    candidate, suffix = base, 1
    while candidate.exists():
        candidate = parent / f"{base.name}-{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


# ── verify the install that just happened ──────────────────────────────────────
def smoke_test(venv_python: Path, timeout: int = 120) -> dict:
    """Import the installed server in a FRESH interpreter and report what it exposes.

    Runs with cwd in a neutral temp dir on purpose: with the repo as cwd, ``import mcp_server``
    resolves to the working tree and the test would pass on code that was never installed.
    """
    with tempfile.TemporaryDirectory(prefix="psamvault-smoke-") as neutral:
        proc = subprocess.run(
            [str(venv_python), "-c", SMOKE_CODE], cwd=neutral, capture_output=True, text=True,
            timeout=timeout,
        )
    if proc.returncode != 0:
        return {"ok": False, "version": None, "tools": [], "detail": (proc.stderr or proc.stdout)[-800:]}
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "version": None, "tools": [], "detail": f"unreadable smoke output: {exc}"}
    tools = sorted(payload.get("tools") or [])
    ok = bool(payload.get("version")) and bool(tools)
    return {"ok": ok, "version": payload.get("version"), "tools": tools,
            "detail": "" if ok else "the installed package exposes no tools"}


def rollback(previous_version: str | None, venv_python: Path, timeout: int = 600) -> dict:
    """Put the previously installed release back after a failed upgrade.

    Only a *released* previous version can be restored this way; a previous git build has no
    artifact to reinstall, so the caller is told rather than left thinking it was rolled back.
    """
    if not previous_version:
        return {"ok": False, "message": "no previous version recorded — nothing to roll back to"}
    cmd = ["uv", "pip", "install", "--python", str(venv_python), "--refresh",
           f"psamvault-mcp=={previous_version}"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode == 0:
        return {"ok": True, "message": f"rolled back to psamvault-mcp=={previous_version}"}
    return {"ok": False, "message": f"rollback to {previous_version} failed:\n"
                                     f"{(proc.stderr or proc.stdout)[-800:]}"}
