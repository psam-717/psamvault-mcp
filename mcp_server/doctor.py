"""`psamvault-mcp doctor` — diagnose (and optionally repair) installation drift.

Why this exists: pipx links a package's console scripts **only when pipx itself installs it**. An
upgrade performed with ``uv pip install`` inside the pipx venv therefore leaves pipx's records stale
and any newly added entry point invisible on PATH. That is silent, and it is how the former
``psamvault-compat`` command became unreachable while the file existed in the venv.

The doctor reports:

1. every entry point the installed package declares vs. the apps actually linked in pipx's bin dir
2. the version pipx *thinks* is installed vs. the version that is really installed
3. processes holding the venv (they block a pipx reinstall on Windows)
4. whether the installed package imports in a fresh interpreter
5. the server <-> usage-skill version pairing
6. whether a newer release is published

Exit codes: 0 healthy, 1 findings, 2 a requested repair failed.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path

DIST = "psamvault-mcp"
_PKG = "psamvault_mcp"


def _install_env():
    """Import lazily so doctor still runs (degraded) if install_env is unavailable."""
    try:
        from mcp_server import install_env

        return install_env
    except Exception:
        return None


def _declared_entry_points() -> list[str]:
    """Console scripts this installed package declares."""
    names: list[str] = []
    try:
        eps = importlib.metadata.entry_points()
        group = eps.select(group="console_scripts") if hasattr(eps, "select") else eps.get("console_scripts", [])
        for ep in group:
            dist = getattr(ep, "dist", None)
            dist_name = getattr(dist, "name", None) if dist else None
            if dist_name and str(dist_name).replace("-", "_") == _PKG:
                names.append(ep.name)
    except Exception:
        pass
    return sorted(names)


def _installed_version() -> str | None:
    try:
        return importlib.metadata.version(DIST)
    except importlib.metadata.PackageNotFoundError:
        return None


def _interpreter() -> str:
    return sys.executable


def _pipx_records() -> dict:
    """What pipx believes: the version of this package, and which apps belong to which venv.

    The app ownership matters: the psamvault CLI lives in the same bin directory and exposes
    ``psamvault``/``pv``, so "not declared by psamvault-mcp" is not the same as "stale".
    """
    records: dict = {"version": None, "apps_by_venv": {}}
    try:
        proc = subprocess.run(["pipx", "list", "--json"], capture_output=True, text=True, timeout=120)
        if proc.returncode != 0 or not proc.stdout.strip():
            return records
        data = json.loads(proc.stdout)
        for name, info in (data.get("venvs") or {}).items():
            main_package = (info.get("metadata") or {}).get("main_package", {}) or {}
            apps = main_package.get("apps") or []
            records["apps_by_venv"][str(name)] = [str(a) for a in apps]
            if str(name).replace("_", "-") == DIST:
                records["version"] = main_package.get("package_version")
    except Exception:
        return records
    return records


def _pipx_metadata_version() -> str | None:
    return _pipx_records().get("version")


def _apps_owned_by_other_packages() -> set[str]:
    records = _pipx_records()
    owned: set[str] = set()
    for venv, apps in records.get("apps_by_venv", {}).items():
        if venv.replace("_", "-") != DIST:
            # pipx records 'name.exe'; the bin-dir scan yields stems — compare stems.
            owned.update(_stem(a) for a in apps)
    return owned


def _stem(app_name: str) -> str:
    """Normalise an app name for comparison: drop a trailing .exe, lowercase on Windows."""
    name = str(app_name).strip()
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name.lower() if os.name == "nt" else name


def _fresh_import() -> tuple[bool, str]:
    """Import the installed package in a fresh interpreter from a neutral cwd."""
    code = f"import importlib.metadata as m; print(m.version('{DIST}'))"
    try:
        proc = subprocess.run(
            [_interpreter(), "-c", code],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(Path.home()),
            env={**os.environ, "PYTHONPATH": ""},
        )
        if proc.returncode == 0:
            return True, proc.stdout.strip()
        return False, (proc.stderr or proc.stdout).strip()[:200]
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def diagnose(probe_index: bool = True) -> dict:
    env = _install_env()
    installed = _installed_version()
    declared = _declared_entry_points()
    linked = sorted(env.linked_apps()) if env else []
    missing = [name for name in declared if name not in linked]
    # An app in the bin dir that another pipx package owns (the psamvault CLI, for instance) is not
    # this package's leftover — only flag names nothing else claims.
    others = _apps_owned_by_other_packages()
    stale_linked = [name for name in linked if name not in declared and _stem(name) not in others]

    holders: list[dict] = []
    venv_free = False  # fails busy: a probe that cannot answer must never read as "free"
    if env:
        try:
            holders = env.holders()
        except Exception:
            holders = []
        try:
            venv_free = bool(env.is_free())
        except Exception:
            venv_free = False
    # No named holder AND not provably free => the probe itself failed (no powershell/pgrep, timeout,
    # unparseable output). Say so instead of implying the venv is idle: `--fix` recreates the venv,
    # and doing that under a live server is the one failure this module exists to prevent.
    probe_uncertain = bool(env) and not holders and not venv_free

    pipx_version = _pipx_metadata_version()
    imports_ok, imports_detail = _fresh_import()

    skill: dict = {}
    try:
        from mcp_server import compat

        report = compat.check(probe_index=probe_index)
        skill = {
            "installed_skill": report.get("installed_skill"),
            "skill_floor": report.get("skill_floor"),
            "skill_drift": report.get("skill_drift"),
            "latest_published": report.get("latest_published"),
            "published_newer": report.get("published_newer"),
        }
    except Exception as exc:  # noqa: BLE001
        skill = {"error": f"{type(exc).__name__}: {exc}"}

    findings: list[str] = []
    if installed is None:
        findings.append(
            f"{DIST} is not installed in this interpreter ({_interpreter()}) — "
            "run doctor with the venv's own python"
        )
    if missing:
        findings.append(
            "entry point(s) declared by the package but NOT linked on PATH: "
            f"{', '.join(missing)} — pipx has not re-read this package's scripts"
        )
    if stale_linked:
        findings.append(
            f"app(s) linked on PATH that this version no longer declares: {', '.join(stale_linked)} "
            "— a leftover from a previous release"
        )
    if pipx_version and installed and pipx_version != installed:
        findings.append(
            f"pipx records say {pipx_version} but {installed} is installed "
            "(an upgrade was performed outside pipx)"
        )
    if holders:
        findings.append(
            f"{len(holders)} process(es) hold the venv — pipx cannot recreate it until they stop"
        )
    if probe_uncertain:
        findings.append(
            "could not confirm whether any process holds the venv (the process probe failed) — "
            "assuming it is BUSY; repair will use the in-venv path"
        )
    if not imports_ok:
        findings.append(f"the installed package does not import cleanly: {imports_detail}")
    if skill.get("skill_drift"):
        findings.append(
            f"usage skill {skill.get('installed_skill')} is BELOW the floor "
            f"{skill.get('skill_floor')} — run: psamvault-mcp compat --sync-skill"
        )
    if skill.get("published_newer"):
        findings.append(
            f"{skill['published_newer']} is published and newer — run: "
            "psamvault-mcp compat --apply --latest"
        )

    return {
        "interpreter": _interpreter(),
        "installed_version": installed,
        "pipx_metadata_version": pipx_version,
        "declared_entry_points": declared,
        "linked_apps": linked,
        "missing_links": missing,
        "stale_links": stale_linked,
        "venv_holders": holders,
        "venv_free": venv_free,
        "venv_probe_uncertain": probe_uncertain,
        "fresh_import_ok": imports_ok,
        "fresh_import_detail": imports_detail,
        "skill": skill,
        "findings": findings,
        "exit_code": 0 if not findings else 1,
    }


def render(report: dict) -> str:
    lines = [
        "psamvault-mcp doctor",
        f"  interpreter      : {report['interpreter']}",
        f"  installed        : {report['installed_version'] or 'not installed here'}",
        f"  pipx records     : {report['pipx_metadata_version'] or 'unknown'}"
        + ("" if report["pipx_metadata_version"] == report["installed_version"] else "   [stale]"),
        f"  entry points     : {', '.join(report['declared_entry_points']) or 'none declared'}",
        f"  linked on PATH   : {', '.join(report['linked_apps']) or 'none'}",
        f"  venv             : {'free' if report['venv_free'] else (str(len(report['venv_holders'])) + ' process(es) holding it' if report['venv_holders'] else 'unknown (probe failed) — treated as busy')}",
        f"  fresh import     : {'ok' if report['fresh_import_ok'] else 'FAILED — ' + report['fresh_import_detail']}",
    ]
    skill = report.get("skill") or {}
    if skill and not skill.get("error"):
        lines.append(
            f"  skill floor      : {skill.get('skill_floor')}  (installed {skill.get('installed_skill')})"
        )
        lines.append(
            f"  newest published : {skill.get('latest_published') or 'unknown (PyPI unreachable — advisory)'}"
        )
    if report["findings"]:
        lines.append("findings:")
        lines.extend(f"  - {f}" for f in report["findings"])
        if not report["venv_free"]:
            lines.append("fix (when no sessions are running):")
            lines.append("  stop the gateway and retire the MCP processes, then:")
            lines.append(f"  pipx install --force {DIST}=={report['installed_version'] or '<version>'}")
        else:
            lines.append("fix: psamvault-mcp doctor --fix")
    else:
        lines.append("healthy — nothing to fix")
    return "\n".join(lines)


def _fix(report: dict) -> int:
    """Relink + refresh pipx's records by reinstalling the exact installed version."""
    from mcp_server import compat

    env = _install_env()
    if env is None:
        print("cannot repair: install_env is unavailable in this build")
        return 2
    if not report["venv_free"]:
        print(
            "refusing to repair while the venv is in use — pipx recreates the venv, and Windows will "
            "not replace a running python.exe.\nstop the gateway first, then re-run: psamvault-mcp doctor --fix"
        )
        return 2
    version = report["installed_version"]
    if not version:
        print("cannot repair: the installed version could not be determined")
        return 2
    result = compat._install(version, force_uv=False, assume_free=True)
    print(f"reinstall {DIST}=={version} via {result.get('installer')}: {'ok' if result['ok'] else 'FAILED'}")
    if not result["ok"]:
        print(result.get("stderr") or result.get("stdout"))
        return 2
    after = diagnose(probe_index=False)
    print(render(after))
    return after["exit_code"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="psamvault-mcp doctor",
        description="Diagnose (and optionally repair) psamvault-mcp installation drift.",
    )
    parser.add_argument("--fix", action="store_true", help="repair: relink entry points / refresh pipx records")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--no-index", action="store_true", help="skip the PyPI probe")
    args = parser.parse_args(argv)

    report = diagnose(probe_index=not args.no_index)
    print(json.dumps(report, indent=2) if args.json else render(report))

    if args.fix and report["findings"]:
        return _fix(report)
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
