"""Cron detector: is the installed psamvault MCP paired with its matching skill version?

Silent when in sync (a cron job must stay quiet), prints a drift report and exits 1 when the pair
needs attention. The contract ships inside the installed package, so this needs no network and no
agent session. Cron-env safe: .py only, absolute interpreter, cleared PYTHONPATH.

Installed copy: ``$HERMES_HOME/scripts/psamvault-compat-check.py`` (this file is the source of truth).
"""

import json
import os
import subprocess
import sys

VENV_PYTHON = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "pipx", "pipx", "venvs", "psamvault-mcp", "Scripts", "python.exe"
)
FALLBACK_PYTHON = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "hermes", "hermes-agent", "venv", "Scripts", "python.exe"
)
PROBE = "from mcp_server.compat import main; import sys; sys.exit(main(['--json']))"


def run(python_exe: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [python_exe, "-c", PROBE],
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "PYTHONPATH": ""},
    )


def main() -> int:
    for python_exe in (VENV_PYTHON, FALLBACK_PYTHON):
        if not os.path.isfile(python_exe):
            continue
        try:
            proc = run(python_exe)
        except Exception as exc:  # noqa: BLE001 — a hung probe must not wedge the cron tick
            print(json.dumps({"psamvault_compat": "probe failed", "error": f"{type(exc).__name__}: {exc}"}))
            return 1
        if not proc.stdout.strip():
            print(json.dumps({"psamvault_compat": "no output", "stderr": proc.stderr[-500:]}))
            return 1
        try:
            report = json.loads(proc.stdout)
        except json.JSONDecodeError:
            print(json.dumps({"psamvault_compat": "unparseable output", "stdout": proc.stdout[-500:]}))
            return 1

        if report.get("exit_code") == 0:
            return 0  # in sync — stay silent

        print(json.dumps({
            "psamvault_compat": "DRIFT",
            "installed_mcp": report.get("installed_mcp"),
            "target_mcp": report.get("target_mcp"),
            "installed_skill": report.get("installed_skill"),
            "expected_skill": report.get("expected_skill"),
            "tool_drift": report.get("tool_drift"),
            "breaking_pending": report.get("breaking_pending"),
            "findings": report.get("findings"),
            "apply_command": "psamvault-compat --apply"
            + (" --allow-breaking" if report.get("breaking_pending") else ""),
        }, indent=2))
        return 1
    print(json.dumps({"psamvault_compat": "no python found", "tried": [VENV_PYTHON, FALLBACK_PYTHON]}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
