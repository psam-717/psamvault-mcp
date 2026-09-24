"""Regression tests for the run_with_credential subprocess runner.

The bug these pin down: ``communicate()`` waits for the child's PIPES to reach EOF, not for the
child to exit. A launcher that spawns a long-lived grandchild inheriting those pipes — exactly what
a uv-tool ``.exe`` shim does (``twine.exe`` → python) — keeps them open, so a command that finished
in milliseconds is reported as "timed out" with empty output, and the still-running process is
abandoned instead of killed.
"""
from __future__ import annotations

import asyncio
import os
import signal
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


async def test_runner_does_not_depend_on_loop_subprocess_support(monkeypatch):
    """Invariant: the runner must work even when the event loop cannot create subprocesses.

    Live symptom this pins (MCP server context): asyncio's Windows subprocess transport created the
    child but the child never executed its own code and never exited, so every call hit the deadline
    with empty output. Running the command on a worker thread with a blocking Popen works
    regardless of the loop's subprocess support.
    """
    async def _unsupported(*args, **kwargs):
        raise NotImplementedError("loop has no subprocess support")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _unsupported)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _unsupported)

    result = await run_command_with_credential(
        command=f'"{sys.executable}" -c "print(7)"',
        credential_value="pypi-token-abcdef123456",
        inject_as="env",
        env_var_name="TWINE_PASSWORD",
        timeout=30,
    )

    assert result.get("error") is None, f"runner leaned on the event loop: {result.get('error')!r}"
    assert result["exit_code"] == 0
    assert "7" in result["stdout"]


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


# ── The timeout kill must never signal the CALLER's process group ────────────────────────────────

@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX-only; Windows kills by PID tree")
async def test_timed_out_command_runs_in_its_own_process_group(tmp_path):
    """The command must be in its own session/group, so killing its tree cannot reach the caller.

    Regression this pins: the child inherited the caller's process group and the timeout handler called
    ``os.killpg(os.getpgid(child))`` — which signals the CALLER's group. In production the caller is the
    MCP server, so a single timed-out ``run_with_credential`` killed the server; in CI it killed pytest
    and the step shell (exit 137, and no log to say why).
    """
    pid_file = tmp_path / "group.pid"
    pgid_file = tmp_path / "group.pgid"
    script = _write_script(tmp_path, "slow_group.py", f"""
        import os, time
        open(r"{pid_file}", "w").write(str(os.getpid()))
        open(r"{pgid_file}", "w").write(str(os.getpgrp()))
        print("working", flush=True)
        time.sleep(60)
    """)
    try:
        result = await run_command_with_credential(
            command=f'"{sys.executable}" "{script}"',
            credential_value="«redacted:test-credential»",
            inject_as="env",
            env_var_name="TWINE_PASSWORD",
            timeout=5,
        )
        assert result["error"] == "timeout"
        assert pgid_file.exists(), "the command never recorded its process group — did it start?"
        own_group = int(pgid_file.read_text().strip())
        assert own_group != os.getpgrp(), (
            "the timed-out command ran in the caller's process group "
            f"({own_group}); the timeout kill would have signalled the caller"
        )
    finally:
        if pid_file.exists():
            _kill(int(pid_file.read_text().strip() or 0))


async def test_kill_falls_back_to_the_pid_when_the_group_is_ours(monkeypatch):
    """Defence in depth: even sharing a group must not let the kill reach the caller."""
    if os.name == "nt":  # pragma: no cover - POSIX path only
        pytest.skip("POSIX branch")
    from mcp_server import cmd_runner as cr

    calls: dict[str, tuple] = {}
    monkeypatch.setattr(cr.os, "getpgid", lambda pid: os.getpgrp())
    monkeypatch.setattr(cr.os, "killpg", lambda pgid, sig: calls.setdefault("killpg", (pgid, sig)))
    monkeypatch.setattr(cr.os, "kill", lambda pid, sig: calls.setdefault("kill", (pid, sig)))

    cr._kill_process_tree(4242)

    assert "killpg" not in calls, "signalled our own process group"
    assert calls.get("kill") == (4242, signal.SIGKILL)
