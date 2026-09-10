"""Subprocess runner with credential injection and output redaction.

This module provides the core mechanism for ``run_with_credential``:
spawns a subprocess with a credential injected via environment variable
or stdin pipe, captures output, and redacts the credential value before
returning — so the calling agent never sees the secret.

The function also blocks commands that could leak credentials from the
psamvault CLI itself (``psamvault get``, ``psamvault show``,
``psamvault ak-get``, ``psamvault search``, and similar).
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
import subprocess
import tempfile
from typing import Optional

# Commands that are blocked from run_with_credential because they
# could leak stored credentials back to the agent via stdout.
_BLOCKED_COMMANDS: list[re.Pattern[str]] = [
    # psamvault CLI read operations
    re.compile(r"\bpsamvault\s+(get|show|ak-get|ak-show|search|list|export)\b", re.I),
    re.compile(r"\bpsamvault\s+(vault-get|credential-get|entry-get)\b", re.I),
    # Generic key/credential read operations that could dump secrets
    re.compile(r"\bcat\s+.*\.psamvault", re.I),
    re.compile(r"\btype\s+.*\.psamvault", re.I),
]


def _is_command_blocked(command: str) -> str | None:
    """Return an error message if the command is blocked, else None."""
    for pattern in _BLOCKED_COMMANDS:
        if pattern.search(command):
            return (
                f"Command contains a blocked operation: "
                f"'{pattern.pattern}'. "
                f"This command could leak credentials. "
                f"Use the appropriate MCP tool instead."
            )
    return None


async def run_command_with_credential(
    command: str,
    credential_value: str,
    inject_as: str = "env",
    env_var_name: Optional[str] = None,
    extra_env: Optional[dict[str, str]] = None,
    workdir: Optional[str] = None,
    timeout: int = 120,
) -> dict:
    """Run a shell command with a credential injected via env var or stdin.

    The credential value NEVER appears in the returned dict — all output
    is scanned for the credential value and replaced with ``[REDACTED]``.

    Args:
        command:          Shell command to run (e.g. ``"twine upload dist/*"``).
        credential_value: The plaintext credential (scoped to this function
                          call only — never serialised or logged).
        inject_as:        ``"env"`` — set as environment variable (default).
                          ``"stdin"`` — pipe as stdin to the subprocess.
                          Only ``"env"`` and ``"stdin"`` are supported.
        env_var_name:     Required when ``inject_as="env"``. The environment
                          variable name to set (e.g. ``"TWINE_PASSWORD"``).
                          When set to ``"TWINE_PASSWORD"``, also sets
                          ``TWINE_USERNAME=__token__`` as a convenience.
        extra_env:        Optional additional env vars (non-sensitive). These
                          are merged into the subprocess environment.
        workdir:          Working directory for the subprocess. Passed as
                          ``cwd`` to the subprocess. Defaults to the MCP
                          server's CWD if not provided.
        timeout:          Max seconds to wait for the command to complete.
                          Default 120. Set higher for long-running operations
                          (e.g. large uploads, builds).

    Returns:
        A dict with keys:
        - ``exit_code``: int — subprocess return code (-1 for errors)
        - ``stdout``: str — stdout with credential redacted
        - ``stderr``: str — stderr with credential redacted
        - ``error``: str — error message if command could not run (absent on success)
    """
    # Block dangerous commands before doing anything else
    blocked = _is_command_blocked(command)
    if blocked:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": blocked,
            "error": blocked,
        }

    # Build environment
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    stdin_data: Optional[bytes] = None

    if inject_as == "env":
        if not env_var_name:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": "env_var_name is required when inject_as='env'",
                "error": "env_var_name is required when inject_as='env'",
            }
        env[env_var_name] = credential_value
        # Convenience: for PyPI token uploads, also set TWINE_USERNAME
        if env_var_name.upper() == "TWINE_PASSWORD":
            env["TWINE_USERNAME"] = "__token__"

    elif inject_as == "stdin":
        stdin_data = credential_value.encode("utf-8")

    else:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Unknown inject_as mode: '{inject_as}'. Use 'env' or 'stdin'.",
            "error": f"Unknown inject_as mode: '{inject_as}'. Use 'env' or 'stdin'.",
        }

    # Run the command on a WORKER THREAD with a blocking Popen. Two independent traps live here:
    #
    # 1. asyncio's Windows subprocess transport cannot be trusted in the MCP server's event loop:
    #    children were created but never executed their own code and never exited, so every call
    #    ended in the deadline ("Command timed out after Ns", empty output) for work that would run
    #    the same way in milliseconds from a plain process. A blocking Popen on a thread sidesteps
    #    the transport entirely.
    # 2. Never hand a pipe to a command whose launcher spawns a long-lived grandchild (uv-tool .exe
    #    shims: twine.exe -> python). The grandchild inherits the pipe handles, so waiting for EOF
    #    blocks long after the command itself finished — that is what reported finished uploads as
    #    timeouts. Files have no EOF to wait on.
    with tempfile.TemporaryDirectory(prefix="psamvault-run-",
                                     ignore_cleanup_errors=True) as tmp:
        out_path = os.path.join(tmp, "stdout")
        err_path = os.path.join(tmp, "stderr")
        try:
            timed_out, returncode, stdout_text, stderr_text = await asyncio.to_thread(
                _run_blocking, command, env, workdir, timeout, stdin_data, out_path, err_path)
        except Exception as e:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "error": str(e),
            }
    stdout_text = _redact(stdout_text, credential_value)
    stderr_text = _redact(stderr_text, credential_value)

    if timed_out:
        # Hand back whatever the command managed to print — a partial result is evidence, an empty
        # string is not. The credential is already redacted above.
        return {
            "exit_code": -1,
            "stdout": stdout_text,
            "stderr": stderr_text or f"Command timed out after {timeout}s",
            "error": "timeout",
            "timed_out": True,
        }

    return {
        "exit_code": returncode or 0,
        "stdout": stdout_text,
        "stderr": stderr_text,
    }


def _run_blocking(
    command: str,
    env: dict,
    workdir: Optional[str],
    timeout: int,
    stdin_bytes: Optional[bytes],
    out_path: str,
    err_path: str,
) -> tuple[bool, Optional[int], str, str]:
    """Run ``command`` with a blocking Popen (worker thread); returns
    ``(timed_out, returncode, stdout, stderr)``.

    ``stdin`` is a file (or DEVNULL), never a pipe: with ``inject_as='stdin'`` the credential is
    written to a temp file first, so nothing here depends on pipe semantics at all.
    """
    stdin_file = None
    try:
        stdin_src = subprocess.DEVNULL
        if stdin_bytes is not None:
            stdin_path = out_path + ".stdin"
            with open(stdin_path, "wb") as fh:
                fh.write(stdin_bytes)
            stdin_file = open(stdin_path, "rb")
            stdin_src = stdin_file
        with open(out_path, "wb") as out_f, open(err_path, "wb") as err_f:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdin=stdin_src,
                stdout=out_f,
                stderr=err_f,
                env=env,
                cwd=workdir,
            )
            try:
                proc.wait(timeout=timeout)
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                # Kill the tree, not just the launcher: the real worker is usually a grandchild.
                _kill_process_tree(proc.pid)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=15)
        return timed_out, proc.returncode, _read_output_file(out_path), _read_output_file(err_path)
    finally:
        if stdin_file is not None:
            stdin_file.close()


def _read_output_file(path: str) -> str:
    """Decoded contents of a captured-output file (empty string when it could not be read)."""
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _redact(text: str, credential_value: str) -> str:
    """Replace the credential (and its 8-char prefix) so it can never reach the caller."""
    if not credential_value or not text:
        return text
    text = text.replace(credential_value, "[REDACTED]")
    if len(credential_value) > 8:
        text = text.replace(credential_value[:8], "[REDACTED]")
    return text


def _kill_process_tree(pid: int) -> None:
    """Kill a child AND everything it spawned (best effort)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=30)
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        pass
