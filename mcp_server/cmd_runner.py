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
import os
import re
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

    # Run the command
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=workdir,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(input=stdin_data),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "error": "timeout",
        }
    except Exception as e:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "error": str(e),
        }

    stdout_text = stdout_bytes.decode("utf-8", errors="replace")
    stderr_text = stderr_bytes.decode("utf-8", errors="replace")

    # Redact the credential value from all output
    if credential_value:
        stdout_text = stdout_text.replace(credential_value, "[REDACTED]")
        stderr_text = stderr_text.replace(credential_value, "[REDACTED]")

        # Also redact first 8 chars (common partial leak pattern)
        if len(credential_value) > 8:
            prefix = credential_value[:8]
            stdout_text = stdout_text.replace(prefix, "[REDACTED]")
            stderr_text = stderr_text.replace(prefix, "[REDACTED]")

    return {
        "exit_code": proc.returncode or 0,
        "stdout": stdout_text,
        "stderr": stderr_text,
    }
