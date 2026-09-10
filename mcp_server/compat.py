"""MCP ↔ skill version lockstep.

The server and its usage skill version independently, so a mismatch is invisible: the skill can
document a tool the server no longer has (v0.5.0 removed a tool and added another, leaving the tool
COUNT unchanged at 13 — a count check cannot see that). This module ships a machine-readable contract
inside the wheel, checks the *installed* server against it, and can apply the matching pair.

Authority: the installed server wins. The skill is pulled to the version pinned for the installed
server, never the other way round. A release marked ``breaking`` is never applied without
``--allow-breaking`` — a silently disappearing tool is exactly the change a human should see.

CLI: ``psamvault-compat`` (``--check`` default, ``--json``, ``--apply``, ``--allow-breaking``).
Exit codes: 0 in sync, 1 drift found, 2 refused (breaking without the flag).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CONTRACT_FILENAME = "compatibility.json"
FRONTMATTER_VERSION = re.compile(r"^version:\s*([0-9][0-9A-Za-z.\-+]*)\s*$", re.MULTILINE)
DEFAULT_CLONE = Path("D:/Projects/py-projects/private-skills")


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
    expected_skill = effective["skill"]
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

    skill_drift = skill_current != expected_skill
    if skill_drift:
        findings.append(
            f"skill version {skill_current!r} != {expected_skill} pinned for server "
            f"{effective['mcp']} ({skill_location})"
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
        f"  skill installed  : {report['installed_skill']}",
        f"  skill expected   : {report['expected_skill']}",
        f"  tools            : {'+%d/-%d vs contract' % (len(report['tool_drift']['unexpected']), len(report['missing'])) if not report['in_sync'] else 'match contract'}",
    ]
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


def _install(target_version: str) -> dict:
    """Install the target release into the pipx venv (uv, WITH deps — --no-deps drops tools)."""
    cmd = ["uv", "pip", "install", "--python", str(pipx_python()), f"psamvault-mcp=={target_version}"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return {"ok": proc.returncode == 0, "cmd": cmd, "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:]}


def _skill_blob_for_version(skill_version: str, entry: dict) -> str | None:
    """Fetch the skill text whose frontmatter matches a version, from the clone's history."""
    repo, rel = clone_path(), load_contract()["skill"]["path"]
    if not (repo / ".git").is_dir():
        return None
    found = subprocess.run(
        ["git", "-C", str(repo), "log", "--all", "--format=%H", "-S", f"version: {skill_version}", "--", rel],
        capture_output=True, text=True, timeout=120,
    )
    shas = [line.strip() for line in found.stdout.splitlines() if line.strip()]
    for sha in shas:
        blob = subprocess.run(
            ["git", "-C", str(repo), "show", f"{sha}:{rel}"], capture_output=True, text=True, timeout=120
        )
        if blob.returncode == 0:
            match = FRONTMATTER_VERSION.search(blob.stdout[:2000])
            if match and match.group(1) == skill_version:
                return blob.stdout
    return None


def _sync_skill(entry: dict) -> dict:
    target = installed_skill_path()
    blob = _skill_blob_for_version(entry["skill"], entry)
    if blob is None:
        return {"ok": False, "reason": f"no commit in {clone_path()} carries skill version {entry['skill']}"}
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        backup = target.with_suffix(".md.bak")
        backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    target.write_text(blob, encoding="utf-8")
    return {"ok": True, "path": str(target), "version": read_skill_version(target)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="psamvault-compat",
        description="Check (and optionally repair) the psamvault-mcp ↔ skill version pairing.",
    )
    parser.add_argument("--check", action="store_true", help="report drift (default)")
    parser.add_argument("--apply", action="store_true", help="install the target release and sync the skill")
    parser.add_argument("--allow-breaking", action="store_true", help="permit applying a breaking release")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--installed-version", default=None, help="override the detected version (diagnostics)")
    parser.add_argument("--skill-path", default=None, help="override the installed skill path (diagnostics)")
    args = parser.parse_args(argv)

    report = check(installed_version=args.installed_version, skill_path=args.skill_path)
    print(json.dumps(report, indent=2) if args.json else render(report))

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
    installed = _install(entry["mcp"])
    print(f"install psamvault-mcp=={entry['mcp']}: {'ok' if installed['ok'] else 'FAILED'}")
    if not installed["ok"]:
        print(installed["stderr"] or installed["stdout"])
        return 1
    synced = _sync_skill(entry)
    print(f"skill -> {entry['skill']}: {'ok' if synced['ok'] else 'FAILED'} ({synced})")

    after = check(installed_version=entry["mcp"], skill_version=read_skill_version(args.skill_path))
    print(render(after))
    print("restart the gateway/session so the running server picks up the new version")
    return after["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
