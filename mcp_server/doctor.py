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

    ``ok`` distinguishes "pipx says there are no other owners" from "pipx could not be asked". Without
    it, a host with no pipx on PATH looks like a host where every unclaimed app belongs to this
    package — and the CLI's own ``psamvault`` gets reported as our leftover.
    """
    records: dict = {"version": None, "apps_by_venv": {}, "ok": False}
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
    records["ok"] = True
    return records


def _apps_owned_by_other_packages(records: dict | None = None) -> set[str] | None:
    """Apps that other pipx packages own — or ``None`` when pipx could not be asked.

    ``None`` is not the same as "nobody else owns anything": with no pipx on PATH the ownership of an
    unclaimed app in the bin dir is simply unknown, and guessing "ours, therefore stale" blames this
    package for the CLI's own ``psamvault``.
    """
    records = _pipx_records() if records is None else records
    if not records.get("ok"):
        return None
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


def _version_installed_in(python: str) -> str | None:
    """The psamvault-mcp version installed in ``python``'s environment, or None. Never raises."""
    code = f"import importlib.metadata as m; print(m.version('{DIST}'))"
    try:
        proc = subprocess.run(
            [python, "-c", code], capture_output=True, text=True, timeout=60,
            cwd=str(Path.home()), env={**os.environ, "PYTHONPATH": ""},
        )
    except Exception:  # noqa: BLE001
        return None
    return (proc.stdout or "").strip() if proc.returncode == 0 else None


def _pipx_venv_version() -> str | None:
    """What is really installed in the PIPX venv — which is not always the interpreter running us.

    Run `doctor` from a sandbox (or any other venv) and `importlib.metadata` describes THAT
    environment, while the bin dir and pipx's records describe the pipx one. Comparing the two
    directly invents drift; the honest comparison is pipx's records against pipx's own venv.
    """
    try:
        env = _install_env()
        python = str(env.venv_python())
    except Exception:  # noqa: BLE001
        return None
    if not python or os.path.normcase(python) == os.path.normcase(_interpreter()):
        return None  # same interpreter: the running version already IS the pipx version
    return _version_installed_in(python)


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
    pipx_records = _pipx_records()
    others = _apps_owned_by_other_packages(pipx_records)
    stale_linked = (
        [name for name in linked if name not in declared and _stem(name) not in others]
        if others is not None
        else []  # pipx could not be asked, so ownership is unknown — do not guess "ours"
    )

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

    pipx_version = pipx_records.get("version")
    pipx_reality = _pipx_venv_version()
    pipx_known = bool(pipx_records.get("ok"))
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
            # The command compat already worked out, --allow-breaking included when the target is newer
            # than the installed contract. Building the text here is how the advice drifts out of sync
            # with the gate that must accept it.
            "apply_command": report.get("apply_command"),
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
    # pipx's records describe the PIPX venv, so compare them against that venv's own version —
    # `installed` only when doctor is run the normal way (through the pipx shim), else it describes
    # whatever interpreter happens to be running us and the comparison would invent drift.
    pipx_actual = pipx_reality or installed
    if pipx_version and pipx_actual and pipx_version != pipx_actual:
        findings.append(
            f"pipx records say {pipx_version} but {pipx_actual} is installed "
            "(an upgrade was performed outside pipx)"
        )
    if holders:
        named = ", ".join(f"{h.get('name') or '?'}({h['pid']})" for h in holders)
        findings.append(
            f"{len(holders)} process(es) hold the venv ({named}) — pipx cannot recreate it until they stop"
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
            + (skill.get("apply_command") or "psamvault-mcp compat --apply --latest --allow-breaking")
        )

    # What `--fix` can actually repair: entry points/records/import health. An available update or a
    # stale usage skill is reported with the command that fixes it, and `--fix` must not advertise
    # itself for those — reinstalling the same wheel would be a wasted step that leaves the finding.
    fixable = bool(
        missing
        or stale_linked
        or not imports_ok
        or (pipx_known and pipx_version and pipx_actual and pipx_version != pipx_actual)
    )

    return {
        "interpreter": _interpreter(),
        "installed_version": installed,
        "pipx_metadata_version": pipx_version,
        "pipx_records_ok": pipx_known,
        "pipx_venv_version": pipx_reality,
        "fixable": fixable,
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


def _venv_line(report: dict) -> str:
    """The ``venv`` row: WHO holds it, not just how many.

    A bare count left the reader stuck — the reported repair is "stop whatever holds it", and a
    number cannot be acted on.
    """
    if report["venv_free"]:
        return "free"
    holders = report.get("venv_holders") or []
    if not holders:
        return "unknown (probe failed) — treated as busy"
    named = ", ".join(f"{h.get('name') or '?'}({h['pid']})" for h in holders)
    return f"{len(holders)} process(es) holding it: {named}"


def _holder_kind(holder: dict) -> str:
    line = holder.get("cmdline") or ""
    if "mcp_server.main" in line:
        return "a running MCP server"
    return "an unrelated process carrying the venv path"


def _holder_advice(holders: list) -> str:
    """What to actually stop, derived from the holders themselves.

    The old text said "stop the gateway first" unconditionally. That is wrong whenever the gateway is
    already stopped — the common case, since a stopped-gateway install is exactly when someone tries
    to repair — and it never mentioned the desktop app, whose every open session holds an MCP server
    and which respawns one the moment you kill it.
    """
    if any("mcp_server.main" in (h.get("cmdline") or "") for h in holders):
        return (
            "held by running MCP servers: stop the gateways (`hermes gateway stop --all`) and QUIT the "
            "Hermes desktop app — each open session holds one, and killing the process alone just makes "
            "the app spawn a new one"
        )
    if holders:
        return "held by the processes above — stop them"
    return "the process probe could not confirm the venv is idle — close Hermes sessions and gateways"


def render(report: dict) -> str:
    lines = [
        "psamvault-mcp doctor",
        f"  interpreter      : {report['interpreter']}",
        f"  installed        : {report['installed_version'] or 'not installed here'}",
        f"  pipx records     : "
        + (
            "unknown (pipx not available on PATH)"
            if not report.get("pipx_records_ok", report["pipx_metadata_version"] is not None)
            else (report["pipx_metadata_version"] or "unknown")
            + (
                ""
                if report["pipx_metadata_version"]
                == (report.get("pipx_venv_version") or report["installed_version"])
                else "   [stale]"
            )
        ),
    ] + (
        # Only when we are NOT running inside the pipx venv: otherwise the line above is about this
        # interpreter and the reader would not know which install the numbers describe.
        [f"  pipx venv        : {report['pipx_venv_version']}  (this interpreter is a different install)"]
        if report.get("pipx_venv_version") and report["pipx_venv_version"] != report["installed_version"]
        else []
    ) + [
        f"  entry points     : {', '.join(report['declared_entry_points']) or 'none declared'}",
        f"  linked on PATH   : {', '.join(report['linked_apps']) or 'none'}",
        f"  venv             : {_venv_line(report)}",
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
        if not report.get("fixable", True):
            # Nothing here is an entry-point/records problem: reinstalling the same wheel would be a
            # wasted step that leaves the finding, and the command that DOES fix it is already named.
            lines.append("nothing to relink — run the command named in the finding above")
        elif not report["venv_free"]:
            # Advise the SAME target and the SAME tool `_fix` uses. A raw `pipx install --force
            # psamvault-mcp==<installed>` line prints the running interpreter's version (wrong when
            # doctor is run from a sandbox) and bypasses doctor's own busy/uncertain checks — and this
            # is the path users actually copy, because --fix refuses while holders exist.
            lines.append("fix (when no sessions are running):")
            lines.append("  stop the gateway and retire the MCP processes, then:")
            lines.append("  psamvault-mcp doctor --fix")
            if report.get("venv_probe_uncertain"):
                lines.append(
                    "  (the process probe could not be trusted, so repair will refuse until it can — "
                    "do not recreate the venv by hand)"
                )
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
    if not report.get("fixable", True):
        skill = report.get("skill") or {}
        if skill.get("skill_drift"):
            advice = "psamvault-mcp compat --sync-skill"
        else:
            advice = skill.get("apply_command") or "psamvault-mcp compat --apply"
        print(f"nothing to relink — this install's entry points and records are fine. "
              f"The repair named in the finding is: {advice}")
        return 0
    if not report["venv_free"]:
        print(
            "refusing to repair while the venv is in use — pipx recreates the venv, and Windows will "
            "not replace a running python.exe."
        )
        for holder in report["venv_holders"]:
            print(f"  pid {holder['pid']} {holder.get('name') or '?'} — {_holder_kind(holder)}")
        print(_holder_advice(report["venv_holders"]))
        print("then re-run: psamvault-mcp doctor --fix")
        return 2
    # Repair the version PIPX has — never the interpreter that happens to be running doctor: a sandbox
    # can be running older code, and `pipx install --force` of that version would silently downgrade a
    # working install (and `assume_free=True` would skip the second opinion).
    version = report.get("pipx_venv_version") or report["installed_version"]
    if not version:
        print("cannot repair: the version pipx has installed could not be determined")
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
