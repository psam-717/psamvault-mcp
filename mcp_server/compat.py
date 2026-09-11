"""MCP ↔ skill version lockstep.

The server and its usage skill version independently, so a mismatch is invisible: the skill can
document a tool the server no longer has (v0.5.0 removed a tool and added another, leaving the tool
COUNT unchanged at 13 — a count check cannot see that). This module ships a machine-readable contract
inside the wheel, checks the *installed* server against it, and can apply the matching pair.

Authority: the installed server wins. The skill is brought up to the version the installed server
requires, never the other way round. A release marked ``breaking`` is never applied without
``--allow-breaking`` — a silently disappearing tool is exactly the change a human should see.

Skill versions are a FLOOR, not a pin. Each release records the *minimum* skill version that documents
it; any skill at or above that floor is healthy. This lets the skill move ahead of the MCP — an
improved description of an existing tool is a legitimate skill-only update that needs no release —
while still catching a skill that has fallen behind the server it documents.

CLI: ``psamvault-compat`` (``--check`` default, ``--json``, ``--apply``, ``--sync-skill``,
``--allow-breaking``, ``--from-git``, ``--pull``).
Exit codes: 0 in sync, 1 drift found, 2 refused (breaking without the flag), 3 install failed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from mcp_server import upgrade_safety as safety

CONTRACT_FILENAME = "compatibility.json"
FRONTMATTER_VERSION = re.compile(r"^version:\s*([0-9][0-9A-Za-z.\-+]*)\s*$", re.MULTILINE)
DEFAULT_CLONE = Path("D:/Projects/py-projects/private-skills")
DEFAULT_REPO = Path("D:/Projects/py-projects/psamvault-mcp")


# ── the contract ───────────────────────────────────────────────────────────────
def contract_path() -> Path:
    """The contract ships inside the package, so the installed server reports its own expectation."""
    return Path(__file__).with_name(CONTRACT_FILENAME)


def load_contract(path: str | Path | None = None) -> dict:
    with open(path or contract_path(), encoding="utf-8") as handle:
        return json.load(handle)


def _vkey(version: str) -> tuple:
    parts = re.split(r"[.\-+]", str(version))
    return tuple(int(p) if p.isdigit() else 0 for p in parts[:3])


def releases(contract: dict | None = None) -> list[dict]:
    return (contract or load_contract())["releases"]


def latest_release(contract: dict | None = None) -> dict:
    return max(releases(contract), key=lambda rel: _vkey(rel["mcp"]))


def release_for(version: str, contract: dict | None = None) -> dict | None:
    for rel in releases(contract):
        if rel["mcp"] == str(version):
            return rel
    return None


# ── installed side ─────────────────────────────────────────────────────────────
def installed_version() -> str:
    try:
        from importlib.metadata import version as _dist_version

        return _dist_version("psamvault-mcp")
    except Exception:
        from mcp_server import __version__

        return __version__


def installed_tools() -> list[str]:
    """The tool surface of the code that is running — no subprocess, no session needed."""
    from mcp_server.main import TOOL_DEFINITIONS

    return sorted(tool.name for tool in TOOL_DEFINITIONS)


def installed_skill_path() -> Path:
    home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
    contract = load_contract()
    return home / contract["skill"]["installed_path"]


def read_skill_version(path: str | Path | None = None) -> str | None:
    target = Path(path or installed_skill_path())
    if not target.is_file():
        return None
    head = target.read_text(encoding="utf-8", errors="replace")[:2000]
    match = FRONTMATTER_VERSION.search(head)
    return match.group(1) if match else None


# ── the check ──────────────────────────────────────────────────────────────────
def check(
    installed_version: str | None = None,
    tools: list[str] | None = None,
    skill_version: str | None = None,
    skill_path: str | Path | None = None,
    contract: dict | None = None,
) -> dict:
    contract = contract or load_contract()
    latest = latest_release(contract)
    mcp = installed_version or globals()["installed_version"]()
    tool_list = sorted(tools if tools is not None else installed_tools())
    skill_current = skill_version if skill_version is not None else read_skill_version(skill_path)
    skill_location = str(skill_path or installed_skill_path())

    entry = release_for(mcp, contract)
    # A runtime installed from git can report an older version while already exposing the newer tool
    # surface. When the fingerprint matches the newest release, trust the fingerprint and say so.
    fingerprint_matches_latest = tool_list == sorted(latest["tools"])
    effective = latest if fingerprint_matches_latest else (entry or latest)

    expected_tools = sorted(effective["tools"])
    # The skill version recorded for a release is a FLOOR (minimum that documents it), not an equality:
    # the skill may legitimately move ahead of the MCP without a release.
    skill_floor = effective["skill"]
    expected_skill = skill_floor  # kept for tooling that reads the older key
    missing = [name for name in expected_tools if name not in tool_list]
    unexpected = [name for name in tool_list if name not in expected_tools]

    findings: list[str] = []
    version_drift = mcp != latest["mcp"]
    if version_drift:
        findings.append(f"installed server reports {mcp}, newest contract release is {latest['mcp']}")
        if fingerprint_matches_latest:
            findings.append(
                f"the installed TOOL SURFACE matches {latest['mcp']} — this looks like a "
                f"pre-release/git install still labelled {mcp}"
            )
    if entry is None and not fingerprint_matches_latest:
        known = ", ".join(rel["mcp"] for rel in releases(contract))
        findings.append(f"no contract entry for server {mcp} (known: {known})")

    skill_below_floor = skill_current is None or _vkey(str(skill_current)) < _vkey(skill_floor)
    skill_ahead = (not skill_below_floor) and str(skill_current) != str(skill_floor)
    skill_drift = skill_below_floor  # name kept: drift means "worse than required", never "newer"
    if skill_below_floor:
        findings.append(
            f"skill version {skill_current!r} is BELOW the floor {skill_floor} required by server "
            f"{effective['mcp']} ({skill_location}) — run: psamvault-compat --sync-skill"
        )
    # Where the skill WOULD come from, so a clone parked on an older branch is visible before anyone
    # runs --sync-skill (which refuses to downgrade, but silence is what let this go unnoticed).
    clone_version, _ = read_clone_skill()
    skill_source_stale = bool(
        clone_version and skill_current and _vkey(clone_version) < _vkey(str(skill_current))
    )
    if unexpected:
        findings.append(f"server exposes tools the {effective['mcp']} contract does not have: {unexpected}")
    if missing:
        findings.append(f"server is missing tools the {effective['mcp']} contract declares: {missing}")

    breaking_pending = bool(latest.get("breaking")) and mcp != latest["mcp"]
    if breaking_pending:
        findings.append(
            f"PENDING BREAKING release {latest['mcp']}: REMOVED {latest.get('removed') or 'see notes'}; "
            f"added {latest.get('added') or []}"
        )

    in_sync = bool(
        not skill_drift
        and not unexpected
        and not missing
        and (entry is not None or fingerprint_matches_latest)
    )
    return {
        "installed_mcp": mcp,
        "target_mcp": latest["mcp"],
        "version_drift": version_drift,
        "update_available": version_drift,
        "effective_release": effective["mcp"],
        "expected_skill": expected_skill,
        "skill_floor": skill_floor,
        "skill_ahead": skill_ahead,
        "skill_source": clone_version,
        "skill_source_stale": skill_source_stale,
        "installed_skill": skill_current,
        "skill_drift": skill_drift,
        "skill_path": skill_location,
        "tool_drift": {"missing": missing, "unexpected": unexpected},
        "missing": missing,
        "breaking_pending": breaking_pending,
        "in_sync": in_sync,
        "findings": findings,
        # in_sync means the PAIR is mutually consistent; an update can still be available, which is
        # what a detector must act on (a consistent-but-older pair would otherwise stay silent forever)
        "exit_code": 0 if (in_sync and not version_drift) else 1,
    }


def render(report: dict) -> str:
    lines = [
        "psamvault-mcp compatibility",
        f"  server installed : {report['installed_mcp']}"
        + ("  [sync]" if report["in_sync"] else "  [drift]"),
        f"  server target    : {report['target_mcp']}"
        + ("  (BREAKING)" if report["breaking_pending"] else ""),
        f"  effective release: {report['effective_release']}",
        f"  skill installed  : {report['installed_skill']}"
        + ("  [ahead of the floor — fine]" if report.get("skill_ahead")
           else ("  [BELOW the floor]" if report["skill_drift"] else "")),
        f"  skill floor      : {report['skill_floor']}  (minimum this server requires)",
        f"  tools            : {'+%d/-%d vs contract' % (len(report['tool_drift']['unexpected']), len(report['missing'])) if not report['in_sync'] else 'match contract'}",
    ]
    if report.get("skill_source_stale"):
        lines.append(
            f"  skill in clone   : {report['skill_source']}  [BEHIND installed "
            f"{report['installed_skill']} — the clone is on an older branch; "
            f"--sync-skill will refuse to downgrade]"
        )
    if report["findings"]:
        lines.append("findings:")
        lines.extend(f"  - {finding}" for finding in report["findings"])
    else:
        lines.append("in sync — nothing to do")
    return "\n".join(lines)


# ── apply ──────────────────────────────────────────────────────────────────────
def pipx_python() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "pipx" / "pipx" / "venvs" / "psamvault-mcp" / "Scripts" / "python.exe"
    return Path.home() / ".local" / "pipx" / "venvs" / "psamvault-mcp" / "bin" / "python"


def clone_path() -> Path:
    return Path(os.environ.get("PSAMVAULT_SKILL_CLONE") or DEFAULT_CLONE)


def repo_path() -> Path:
    """The clone `--from-git` installs from (and whose git state `--apply` reports)."""
    return Path(os.environ.get("PSAMVAULT_MCP_REPO") or DEFAULT_REPO)


def _install(target_version: str) -> dict:
    """Install the target release into the pipx venv (uv, WITH deps — --no-deps drops tools).

    ``--refresh`` is deliberate: PyPI's simple index answers with ``cache-control: max-age=600``, so
    right after a release is published uv's cached metadata can still claim the version does not
    exist — the exact "publish, then immediately apply" sequence this command exists for.
    """
    cmd = [
        "uv", "pip", "install", "--python", str(pipx_python()), "--refresh", f"psamvault-mcp=={target_version}"
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return {"ok": proc.returncode == 0, "cmd": cmd, "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:]}


def _install_from_git() -> dict:
    """Install the repo's current code — the normal path for a merged-but-unreleased version."""
    cmd = ["uv", "pip", "install", "--python", str(pipx_python()), str(repo_path())]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    return {"ok": proc.returncode == 0, "cmd": cmd, "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:]}


def target_is_published(version: str, timeout: float = 20.0) -> bool | None:
    """Is this version actually on PyPI? None when the index can't be reached.

    Without this check, applying a contract entry for a not-yet-published release fails deep inside
    the resolver with an opaque "no version of psamvault-mcp==X" error — which is exactly the state a
    contract entry creates between "merged" and "released".
    """
    try:
        import httpx

        response = httpx.get(f"https://pypi.org/pypi/psamvault-mcp/json", timeout=timeout)
        if response.status_code != 200:
            return None
        return version in (response.json().get("releases") or {})
    except Exception:
        return None


def clone_skill_path() -> Path:
    """Where the skill lives inside the clone (the source `--sync-skill` reads)."""
    return clone_path() / load_contract()["skill"]["path"]


def clone_skill_branch() -> str | None:
    """Branch the clone is checked out on — the reason its skill version is what it is."""
    try:
        proc = subprocess.run(["git", "-C", str(clone_path()), "rev-parse", "--abbrev-ref", "HEAD"],
                              capture_output=True, text=True, timeout=60)
        return proc.stdout.strip() or None if proc.returncode == 0 else None
    except Exception:
        return None


def read_clone_skill() -> tuple[str | None, str | None]:
    """The clone's CURRENT skill — its working tree, as-is (mirrors ``--from-git``).

    Returns ``(version, text)``; ``(None, None)`` when there is no skill file there.
    """
    path = clone_skill_path()
    if not path.is_file():
        return None, None
    text = path.read_text(encoding="utf-8", errors="replace")
    match = FRONTMATTER_VERSION.search(text[:2000])
    return (match.group(1) if match else None), text


def _sync_skill(entry: dict, allow_downgrade: bool = False) -> dict:
    """Install the clone's newest skill, provided it passes two guards.

    Both guards exist so the skill can never move *backwards* without being told to:

    * **floor** — the clone's skill must document the installed server (``>= entry["skill"]``);
    * **no silent downgrade** — it must not be older than the skill already installed. The clone is
      read as-is, so a clone parked on an older branch holds an older skill; installing it would
      delete documentation that exists nowhere else, with no error to notice. ``allow_downgrade`` is
      the explicit override.

    Above the floor, a *newer* skill is exactly the point: a skill-only update needs no MCP release.
    """
    floor = entry["skill"]
    version, text = read_clone_skill()
    source = clone_skill_path()
    if text is None:
        return {"ok": False, "reason": f"no skill at {source} (clone {clone_path()})"}
    if version is None:
        return {"ok": False, "reason": f"{source} has no 'version:' in its frontmatter"}
    if _vkey(version) < _vkey(floor):
        return {
            "ok": False,
            "reason": (
                f"the clone's skill is {version} but server {entry['mcp']} requires at least {floor} — "
                f"update the skill in {clone_path()} first"
            ),
        }
    installed = read_skill_version()
    if installed and _vkey(version) < _vkey(str(installed)) and not allow_downgrade:
        branch = clone_skill_branch() or "unknown"
        return {
            "ok": False,
            "reason": (
                f"refusing to downgrade: the clone holds {version} but {installed} is installed "
                f"(clone {clone_path()} is on branch '{branch}'). Update the clone to at least "
                f"{installed}, or pass --allow-downgrade to accept the older skill."
            ),
        }
    target = installed_skill_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        backup = target.with_suffix(".md.bak")
        backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    target.write_text(text, encoding="utf-8")
    return {
        "ok": True,
        "path": str(target),
        "version": read_skill_version(target),
        "floor": floor,
        "source_version": version,
        "replaced": installed,
        "clone_branch": clone_skill_branch(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="psamvault-compat",
        description="Check (and optionally repair) the psamvault-mcp ↔ skill version pairing.",
    )
    parser.add_argument("--check", action="store_true", help="report drift (default)")
    parser.add_argument("--apply", action="store_true", help="install the target release and sync the skill")
    parser.add_argument("--allow-breaking", action="store_true", help="permit applying a breaking release")
    parser.add_argument(
        "--from-git",
        action="store_true",
        help="install the local repo instead of PyPI (for a merged-but-unreleased target)",
    )
    parser.add_argument(
        "--pull",
        action="store_true",
        help="with --from-git: stash local changes, pull --ff-only origin main, restore, then install",
    )
    parser.add_argument(
        "--sync-skill",
        action="store_true",
        help="install the clone's newest skill without touching the MCP (skill-only update)",
    )
    parser.add_argument(
        "--allow-downgrade",
        action="store_true",
        help="permit installing a skill OLDER than the installed one (refused by default)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--installed-version", default=None, help="override the detected version (diagnostics)")
    parser.add_argument("--skill-path", default=None, help="override the installed skill path (diagnostics)")
    args = parser.parse_args(argv)

    report = check(installed_version=args.installed_version, skill_path=args.skill_path)
    print(json.dumps(report, indent=2) if args.json else render(report))

    if args.sync_skill:
        # A skill-only update: the MCP is deliberately untouched, so MCP drift is not a precondition.
        entry = release_for(report["effective_release"]) or latest_release()
        backup = safety.snapshot_skill(installed_skill_path())
        print(f"snapshot: skill backed up to {backup}" if backup
              else "snapshot: no installed skill to back up")
        synced = _sync_skill(entry, allow_downgrade=args.allow_downgrade)
        print(f"skill -> {synced.get('version') or synced.get('reason')}: "
              f"{'ok' if synced['ok'] else 'FAILED'} ({synced})")
        if not synced["ok"]:
            return 3
        after = check(installed_version=report["installed_mcp"],
                      skill_version=read_skill_version(args.skill_path))
        print(render(after))
        return after["exit_code"]

    if not args.apply:
        return report["exit_code"]
    if not report["update_available"]:
        print("in sync with the newest release — nothing to apply")
        return 0
    if report["breaking_pending"] and not args.allow_breaking:
        print(
            f"refusing to apply breaking release {report['target_mcp']} without --allow-breaking "
            "(a tool was removed; confirm before installing)"
        )
        return 2

    entry = latest_release()
    previous = report["installed_mcp"]  # what to put back if the new install does not work
    from_git = args.from_git or args.pull

    backup = safety.snapshot_skill(installed_skill_path())
    print(f"snapshot: skill backed up to {backup}" if backup else "snapshot: no installed skill to back up")

    if args.pull:
        # Upgrade safety (psamvault-cli's model): never pull over uncommitted work, never lose it.
        pulled = safety.stash_and_pull(repo_path())
        print(f"repo: {pulled['message'] or f'pulled {repo_path().name} to origin/main'}")
        if not pulled["ok"]:
            return 1
    if from_git:
        state = safety.git_repo_state(repo_path(), fetch=not args.pull)
        print(f"repo: installing {safety.render_repo_state(state)}")
        if safety.is_pipx_editable():
            print(
                "note: the installed server is an editable/source install — a released wheel replaces "
                "that repo link (--from-git keeps tracking it)"
            )

    published: bool | None = None
    if from_git:
        installed = _install_from_git()
        print(f"install psamvault-mcp from the local repo: {'ok' if installed['ok'] else 'FAILED'}")
    else:
        # The index pre-check is ADVISORY only. PyPI's JSON API lags an upload (CDN cache), so
        # refusing on it produces a false "not published yet" in exactly the publish-then-apply
        # window this command exists for. Attempt the install (--refresh makes it authoritative)
        # and use the index state only to explain a real failure.
        published = target_is_published(entry["mcp"])
        installed = _install(entry["mcp"])
        print(f"install psamvault-mcp=={entry['mcp']}: {'ok' if installed['ok'] else 'FAILED'}")
    if not installed["ok"]:
        print(installed["stderr"] or installed["stdout"])
        if safety.smoke_test(pipx_python())["ok"]:
            print("the installed server still imports — the previous version is intact")
        else:
            print("no working server in the venv — " + safety.rollback(previous, pipx_python())["message"])
        if published is False:
            print(
                f"refusing: {entry['mcp']} is not listed on PyPI, so applying failed in the resolver "
                f"(a contract entry exists the moment a release is merged, before it ships). "
                f"Publish it first, or pass --from-git to install the local repo."
            )
            return 3
        return 1
    if published is None and not from_git:
        print("note: could not reach PyPI to confirm the target is published (install succeeded regardless)")

    smoke = safety.smoke_test(pipx_python())
    if not smoke["ok"]:
        print(f"smoke test FAILED — the installed server does not import: {smoke['detail']}")
        print(safety.rollback(previous, pipx_python())["message"])
        return 1
    print(f"smoke test: {smoke['version']} exposes {len(smoke['tools'])} tools")

    synced = _sync_skill(entry, allow_downgrade=args.allow_downgrade)
    print(f"skill -> {entry['skill']}: {'ok' if synced['ok'] else 'FAILED'} ({synced})")

    after = check(installed_version=entry["mcp"], skill_version=read_skill_version(args.skill_path))
    print(render(after))
    print("restart the gateway/session so the running server picks up the new version")
    return after["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
