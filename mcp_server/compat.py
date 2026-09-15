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

CLI: ``psamvault-mcp compat`` (``--check`` default, ``--json``, ``--apply``, ``--apply --latest``,
``--sync-skill``, ``--from-git``, ``--pull``, ``--allow-breaking``). The standalone ``psamvault-compat``
console script was removed in 0.5.3: pipx links console scripts only for packages it installed itself,
so a script added by a later release never reaches PATH while the file sits in the venv.
Exit codes: 0 in sync, 1 drift found, 2 refused (breaking without the flag), 3 install failed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from mcp_server import upgrade_safety as safety
from mcp_server.versions import is_newer, vkey

try:  # install_env is optional at import time so the module still loads in odd environments
    from mcp_server import install_env
except Exception:  # pragma: no cover - defensive
    install_env = None  # type: ignore[assignment]

from mcp_server import version_check

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
    """Comparable version key. Kept under this name for existing callers; the single
    implementation lives in mcp_server.versions so this module and version_check cannot disagree."""
    return vkey(version)


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
    probe_index: bool = True,
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
            f"{effective['mcp']} ({skill_location}) — run: psamvault-mcp compat --sync-skill"
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

    # What PyPI actually has. The contract only knows releases that shipped WITH this install, so
    # without this the tool is blind to anything newer (an installed 0.5.1 carries a contract whose
    # newest entry is 0.5.1 and would report "nothing to apply" with 0.5.2 published).
    #
    # Advisory by design: an unreachable index must never fail a check, and a newer published
    # release must NOT change the exit code — "an update exists" is not "something is broken".
    published: str | None = None
    published_newer: str | None = None
    if probe_index:
        published = version_check.latest_published()
        if published and is_newer(published, mcp):
            published_newer = published

    # The upgrade advice this report will carry. Reaching a release the installed contract has never
    # seen is breaking-eligible by definition, so that command MUST carry --allow-breaking — the same
    # process refuses it otherwise, which would make every advertised one-liner a dead end.
    if published_newer:
        advice = apply_command(latest=True, breaking=True)
    else:
        advice = apply_command(breaking=bool(breaking_pending))

    return {
        "installed_mcp": mcp,
        "latest_published": published,
        "published_newer": published_newer,
        "pypi_checked": probe_index,
        "apply_command": advice,
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
        "  newest published : "
        + (
            report["latest_published"]
            if report.get("latest_published")
            else ("unknown (PyPI unreachable — advisory)" if report.get("pypi_checked") else "not checked (--no-index)")
        ),
        "  update available : "
        + (
            f"yes — run: {report.get('apply_command') or apply_command(latest=True, breaking=True)}"
            if report.get("published_newer")
            else "no"
        ),
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
    """The venv interpreter pipx owns — the one both the probe and the uv write must agree on.

    Delegates to :func:`install_env.venv_python` on purpose. The busy/free decision comes from that
    module (it also honours ``PSAMVAULT_MCP_VENV`` and, on Windows without ``LOCALAPPDATA``, resolves
    ``Scripts/python.exe``); if the two disagreed, the probe could correctly report "busy" — skipping
    the pipx path — while uv wrote the wheel into a *different* interpreter, leaving the live server
    on the old build. The duplicated join below only runs where install_env cannot answer at all.
    """
    if install_env is not None:
        try:
            return Path(install_env.venv_python())
        except Exception:  # pragma: no cover - defensive; mirrors install_env's own fallbacks
            pass
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "pipx" / "pipx" / "venvs" / "psamvault-mcp" / "Scripts" / "python.exe"
    return Path.home() / ".local" / "pipx" / "venvs" / "psamvault-mcp" / "bin" / "python"


def apply_command(latest: bool = False, breaking: bool = False) -> str:
    """The ``--apply`` invocation the flag gate will ACCEPT — one helper, so advice cannot drift.

    Printing ``--apply --latest`` for a release newer than the installed contract is a dead end: the
    same process refuses it with exit 2 unless ``--allow-breaking`` is present, so a user — or an
    agent woken by the cron detector — copies a command that can never work. Every surface that
    advertises an upgrade (``--check``, ``doctor``, the startup notice, the detector JSON) builds its
    text here.
    """
    parts = ["psamvault-mcp", "compat", "--apply"]
    if latest:
        parts.append("--latest")
    if breaking:
        parts.append("--allow-breaking")
    return " ".join(parts)


def clone_path() -> Path:
    return Path(os.environ.get("PSAMVAULT_SKILL_CLONE") or DEFAULT_CLONE)


def repo_path() -> Path:
    """The clone `--from-git` installs from (and whose git state `--apply` reports)."""
    return Path(os.environ.get("PSAMVAULT_MCP_REPO") or DEFAULT_REPO)


def _install(target_version: str, force_uv: bool = False, assume_free: bool = False) -> dict:
    """Install the target release into the pipx venv.

    Two installers, chosen by whether the venv is free (D9):

    * **pipx, when no MCP process holds the venv** — recreates the venv, which is the only way pipx
      re-reads the package's entry points. This is what keeps `pipx list` honest and keeps newly
      added commands linked on PATH.
    * **uv, when the venv is held** — a running ``python.exe`` cannot be replaced on Windows, so
      ``pipx install --force`` would fail with os error 32. uv installs *into* the existing venv
      (no recreation, no lock), at the cost of leaving pipx's records stale — reported as
      ``relink_pending`` so the user can repair it at a safe moment.

    ``--refresh`` on the uv path is deliberate: PyPI's simple index answers with
    ``cache-control: max-age=600``, so right after a release is published uv's cached metadata can
    still claim the version does not exist — the exact "publish, then immediately apply" sequence
    this command exists for.
    """
    use_pipx = not force_uv and _venv_is_free(assume_free) and _pipx_available()
    if use_pipx:
        cmd = ["pipx", "install", "--force", f"psamvault-mcp=={target_version}"]
        timeout = 900
    else:
        cmd = [
            "uv", "pip", "install", "--python", str(pipx_python()), "--refresh",
            f"psamvault-mcp=={target_version}",
        ]
        timeout = 600
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return {
        "ok": proc.returncode == 0,
        "cmd": cmd,
        "installer": "pipx" if use_pipx else "uv",
        # A uv install cannot relink apps; pipx's metadata is now behind the installed version.
        "relink_pending": not use_pipx,
        "stdout": proc.stdout[-2000:],
        "stderr": proc.stderr[-2000:],
    }


def _pipx_available() -> bool:
    return shutil.which("pipx") is not None


def _venv_is_free(assume_free: bool = False) -> bool:
    """True when nothing holds the venv's python (safe to let pipx recreate it)."""
    if assume_free:
        return True
    if install_env is None:
        return False  # cannot tell -> assume busy -> the uv path, which is always safe
    try:
        return bool(install_env.is_free())
    except Exception:
        return False


def _select_entry(releases: list[dict], target: str | None = None) -> dict:
    """The entry for ``target``, else the newest one by the shared comparator — never ``releases[0]``.

    The contract file happens to be newest-first today; selecting positionally would silently pair a
    successful install with the skill floor and tool fingerprint of a *different* MCP version the first
    time the file is appended to or reordered.
    """
    if target:
        for entry in releases:
            if str(entry.get("mcp")) == str(target):
                return entry
    return max(releases, key=lambda entry: vkey(str(entry.get("mcp", ""))))


def _installed_contract_entry(target: str | None = None) -> dict:
    """Read the release entry from the contract INSIDE the venv (the just-installed version).

    The running process still holds the old contract, so after installing a release newer than this
    build knows about, the skill floor and tool fingerprint must be read from the new code — via a
    fresh interpreter with a neutral cwd.

    The child stays dumb (it prints the whole release list) and the selection happens here, so the
    "which entry" rule lives in exactly one testable place.
    """
    code = (
        "import json, pathlib, mcp_server\n"
        "c = pathlib.Path(mcp_server.__file__).with_name('compatibility.json')\n"
        "print(json.dumps(json.loads(c.read_text(encoding='utf-8'))['releases']))\n"
    )
    try:
        proc = subprocess.run(
            [str(pipx_python()), "-c", code],
            capture_output=True, text=True, timeout=120, cwd=str(Path.home()), env={**os.environ, "PYTHONPATH": ""},
        )
        if proc.returncode == 0 and proc.stdout.strip():
            releases = json.loads(proc.stdout.strip())
            if isinstance(releases, list) and releases:
                return _select_entry(releases, target)
    except Exception:
        pass
    # Fall back to the entry this build already knows about
    return latest_release()


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
        prog="psamvault-mcp compat",
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
    parser.add_argument(
        "--latest",
        action="store_true",
        help="with --apply: target the newest release PUBLISHED on PyPI, not just the contract target",
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="skip the PyPI probe entirely (offline, or a faster check)",
    )
    parser.add_argument("--force-uv", action="store_true", help="always install with uv (never recreate the venv)")
    parser.add_argument(
        "--assume-free",
        action="store_true",
        help="assume no MCP process holds the venv, so pipx may recreate it",
    )
    args = parser.parse_args(argv)

    report = check(
        installed_version=args.installed_version,
        skill_path=args.skill_path,
        probe_index=not args.no_index,
    )
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

    # Which release are we applying? Plain --apply keeps the documented behaviour (the contract
    # target). --latest additionally reaches a release published after this install's contract was
    # built — the only way to leave an install that is already behind.
    contract_target = report["target_mcp"]
    target = contract_target
    if args.latest:
        if report.get("published_newer"):
            target = report["published_newer"]
        else:
            print(
                "in sync with the newest PUBLISHED release — nothing to apply"
                if report.get("latest_published")
                else "cannot tell what is newest: PyPI was unreachable (re-run without --no-index)"
            )
            return 0 if report.get("latest_published") else 1

    unknown_newer = is_newer(target, contract_target)
    if (report["breaking_pending"] or unknown_newer) and not args.allow_breaking:
        if unknown_newer:
            print(
                f"refusing to apply {target} without --allow-breaking: it is newer than anything this "
                f"install's contract knows about ({contract_target}), so its compatibility is unverified "
                "by definition. Re-run with --allow-breaking to accept that."
            )
        else:
            print(
                f"refusing to apply breaking release {target} without --allow-breaking "
                "(a tool was removed; confirm before installing)"
            )
        return 2

    if not report["update_available"] and not unknown_newer:
        print("in sync with the newest release — nothing to apply")
        return 0

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
        published = target_is_published(target)
        installed = _install(target, force_uv=args.force_uv, assume_free=args.assume_free)
        print(
            f"install psamvault-mcp=={target} via {installed.get('installer', '?')}: "
            f"{'ok' if installed['ok'] else 'FAILED'}"
        )
        if installed["ok"] and installed.get("relink_pending"):
            print(
                "note: installed with uv because the venv is in use, so pipx's records were NOT "
                "refreshed. Run `psamvault-mcp doctor` (or `--fix`) when no sessions are running "
                "to relink the entry points and refresh pipx's metadata."
            )
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

    # Read the contract from the version just installed: its skill floor and tool fingerprint are
    # what the pair must now satisfy (the running process still holds the previous contract).
    entry = _installed_contract_entry(target)
    synced = _sync_skill(entry, allow_downgrade=args.allow_downgrade)
    print(f"skill -> {entry.get('skill')}: {'ok' if synced['ok'] else 'FAILED'} ({synced})")

    after = check(
        installed_version=target,
        skill_version=read_skill_version(args.skill_path),
        probe_index=False,
    )
    print(render(after))
    print("restart the gateway/session so the running server picks up the new version")
    return after["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
