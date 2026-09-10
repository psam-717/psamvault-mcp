"""Regression tests for the run_with_credential subprocess runner.

The bug these pin down: ``communicate()`` waits for the child's PIPES to reach EOF, not for the
child to exit. A launcher that spawns a long-lived grandchild inheriting those pipes — exactly what
a uv-tool ``.exe`` shim does (``twine.exe`` → python) — keeps them open, so a command that finished
in milliseconds is reported as "timed out" with empty output, and the still-running process is
abandoned instead of killed.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time

import pytest

from mcp_server.cmd_runner import run_command_with_credential

pytestmark = pytest.mark.asyncio


def _process_alive(pid: int) -> bool:
    """Cross-platform liveness probe. Never use ``os.kill(pid, 0)`` on Windows: CPython maps a
    non-console signal to TerminateProcess, so a "probe" would kill the process it checks."""
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill(pid: int) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=30)
        else:
            os.kill(pid, 9)
    except Exception:
        pass


def _write_script(tmp_path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(path)


@pytest.fixture
def shim_script(tmp_path):
    """A shim-like launcher: spawns a grandchild that INHERITS stdout/stderr, records its pid,
    prints a result, then exits immediately — the parent is done, the pipes are not."""
    pid_file = tmp_path / "grandchild.pid"
    script = _write_script(tmp_path, "shim.py", f"""
        import subprocess, sys
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        open(r"{pid_file}", "w").write(str(child.pid))
        print("upload finished", flush=True)
    """)
    yield script, pid_file
    if pid_file.exists():
        _kill(int(pid_file.read_text().strip() or 0))


@pytest.fixture
def slow_script(tmp_path):
    """A command that genuinely runs longer than the timeout."""
    pid_file = tmp_path / "slow.pid"
    script = _write_script(tmp_path, "slow.py", f"""
        import os, sys, time
        open(r"{pid_file}", "w").write(str(os.getpid()))
        print("starting work", flush=True)
        time.sleep(60)
    """)
    yield script, pid_file
    if pid_file.exists():
        _kill(int(pid_file.read_text().strip() or 0))


async def test_finished_command_is_not_reported_as_timeout(shim_script):
    """The parent exits at once; the grandchild holds the pipes. The runner must report the real
    result instead of blocking on the pipes until the deadline."""
    script, _pid_file = shim_script
    started = time.monotonic()
    result = await run_command_with_credential(
        command=f'"{sys.executable}" "{script}"',
        credential_value="pypi-token-abcdef123456",
        inject_as="env",
        env_var_name="TWINE_PASSWORD",
        timeout=20,
    )
    elapsed = time.monotonic() - started

    assert result.get("error") is None, f"reported {result.get('error')!r} for a command that finished"
    assert result["exit_code"] == 0
    assert "upload finished" in result["stdout"]
    assert elapsed < 10, f"blocked {elapsed:.1f}s on a grandchild-held pipe"


async def test_real_timeout_kills_the_process_tree_and_returns_partial_output(slow_script):
    """A genuine timeout must kill the process (tree) and hand back what was captured — not
    abandon a live process and report an empty string."""
    script, pid_file = slow_script
    result = await run_command_with_credential(
        command=f'"{sys.executable}" "{script}"',
        credential_value="pypi-token-abcdef123456",
        inject_as="env",
        env_var_name="TWINE_PASSWORD",
        timeout=5,
    )

    assert result["error"] == "timeout"
    assert "starting work" in result["stdout"], "partial output was discarded"

    pid = int(pid_file.read_text().strip())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _process_alive(pid):
        time.sleep(0.25)
    assert not _process_alive(pid), f"timed-out process {pid} was left running"
