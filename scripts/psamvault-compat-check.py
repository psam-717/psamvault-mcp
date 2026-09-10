"""Cron detector: is the installed psamvault MCP paired with its matching skill version?

STDOUT IS THE SIGNAL, and the exit code is always 0: this script is used as a cron `monitor` source,
where the engine hashes stdout to decide whether to wake the agent — a non-zero exit is treated as a
script FAILURE, not as "something changed". Empty stdout = in sync (the agent is not woken). A drift
report or an error report = changed output (the agent is woken with the diff).

The `psamvault-compat` CLI keeps real exit codes (0/1/2) for humans and other tooling; this wrapper
deliberately does not. Cron-env safe: .py only, absolute interpreter, cleared PYTHONPATH.

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
    """Always returns 0 — stdout carries the signal (see the module docstring)."""
    for python_exe in (VENV_PYTHON, FALLBACK_PYTHON):
        if not os.path.isfile(python_exe):
            continue
        try:
            proc = run(python_exe)
        except Exception as exc:  # noqa: BLE001 — a hung probe must not wedge the cron tick
            print(json.dumps({"psamvault_compat": "probe failed", "error": f"{type(exc).__name__}: {exc}"}))
            return 0
        if not proc.stdout.strip():
            print(json.dumps({"psamvault_compat": "no output", "stderr": proc.stderr[-500:]}))
            return 0
        try:
            report = json.loads(proc.stdout)
        except json.JSONDecodeError:
            print(json.dumps({"psamvault_compat": "unparseable output", "stdout": proc.stdout[-500:]}))
            return 0

        if report.get("exit_code") == 0:
            return 0  # in sync — print nothing, so the monitor sees no change

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
        return 0
    print(json.dumps({"psamvault_compat": "no python found", "tried": [VENV_PYTHON, FALLBACK_PYTHON]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
