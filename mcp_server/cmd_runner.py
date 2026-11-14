"""Subprocess runner with credential injection and output redaction.

This module provides the core mechanism for ``run_with_credential``:
spawns a subprocess with a credential injected via environment variable
or stdin pipe, captures output, and redacts the credential value before
returning — so the calling agent never sees the secret.

The child is always started with ``PSAMVAULT_AGENT=1``. That is the marker
the psamvault CLI's reveal gate treats as an agent, and ``extra_env`` cannot
override it. A normalised blocklist refuses the obvious secret-revealing
spellings before the process starts. The list is a convenience: the variable
and the CLI gate are the control, and a separate OS user is what isolates
the keychain.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shlex
import signal
import subprocess
import tempfile
from typing import Optional

# Set on every child. Truthy values the CLI counts as an agent are
# 1/true/yes/on (psamvault-cli caller.py). "1" is the value ``--agent`` sets.
_AGENT_ENV = "PSAMVAULT_AGENT"

# ``psamvault`` and ``pv`` are the same entry point (pyproject [project.scripts],
# both ``main:app``). ``python -m main`` is that module.
_CLI_NAMES = frozenset({"psamvault", "pv"})
_PYTHONS = frozenset({"python", "python3", "py"})
_SHELLS = frozenset({"bash", "sh", "dash"})
_POWERSHELL = frozenset({"powershell", "pwsh"})
_FILE_READERS = frozenset({"cat", "type"})

# Secret-revealing command paths, derived from psamvault-cli main
# (github.com/psam-717/psamvault-cli), not from the old regex.
#
# The reveal gate (reveal_gate.py) refuses ``get``, ``ak-get``, ``note-get``,
# and ``export --plaintext`` (including ``--copy``) when this variable is set.
# Grouped forms are the same commands: ``vault get``, ``ak get``, ``note get``.
#
# Also refused here, because that gate does not cover them and they still put
# secret material where the caller can read it:
#   export            decrypts every entry and writes them under a passphrase
#                     the command chooses (command/export_command.py). The
#                     encrypted form is explicitly not gated.
#   generate          prints the generated password (command/vault_commands.py)
#   generate-codes    prints recovery codes (command/recovery_commands.py),
#                     also ``recovery generate-codes``
#   backup create     writes the wrapped vault key (command/backup_commands.py)
#   backup rotate     same, and revokes the other slots
#   dashboard         serves reveals over HTTP (main.py, dashboard/api.py)
#
# ``list`` and ``search`` print no secret. They stay refused in every spelling
# of those two commands, including ``vault list`` and ``vault search``.
# ``site-list``, ``ak-list``, ``note-list``, ``ak list``, and ``note list``
# are different commands and are not here.
#
# ``show``, ``ak-show``, ``vault-get``, ``credential-get``, and ``entry-get``
# are not commands. They are not in this set.
_BLOCKED_PATHS: frozenset[tuple[str, ...]] = frozenset({
    ("get",),
    ("vault", "get"),
    ("ak-get",),
    ("ak", "get"),
    ("note-get",),
    ("note", "get"),
    ("export",),
    ("dashboard",),
    ("generate",),
    ("vault", "generate"),
    ("generate-codes",),
    ("recovery", "generate-codes"),
    ("backup", "create"),
    ("backup", "rotate"),
    ("list",),
    ("vault", "list"),
    ("search",),
    ("vault", "search"),
})

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_MAX_UNWRAP = 8


def _strip_quotes(token: str) -> str:
    """Drop one matching pair of quotes.

    POSIX ``shlex`` already removes them. Windows mode (``posix=False``) keeps
    them on the token, which is not how ``cmd`` hands the word to the program.
    """
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}:
        return token[1:-1]
    return token


def _split_command(command: str, *, posix: bool) -> list[str] | None:
    """Split one command the way the platform shell quotes words.

    ``shlex`` is not a shell. An unmatched quote, or a construct it does not
    tokenise, comes back as None and the string check does not refuse it.
    """
    try:
        return [_strip_quotes(part) for part in shlex.split(command, posix=posix, comments=False)]
    except ValueError:
        return None


def _exe_name(token: str) -> str:
    """Basename, without ``.exe``, case-folded. Slashes of either platform count."""
    name = token.replace("\\", "/").rsplit("/", 1)[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name.casefold()


def _skip_assignments(tokens: list[str]) -> list[str]:
    index = 0
    while index < len(tokens) and _ASSIGNMENT.match(tokens[index]):
        index += 1
    return tokens[index:]


def _after_env(tokens: list[str]) -> list[str]:
    """Drop a leading ``env`` and the flags and assignments that belong to it."""
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if _ASSIGNMENT.match(token):
            index += 1
            continue
        if token in {"-u", "--unset"}:
            index += 2
            continue
        if token.startswith("--unset="):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    return tokens[index:]


def _after_shell_c(rest: list[str], *, posix: bool) -> list[str] | None:
    """The command string after ``bash``/``sh`` ``-c`` / ``-lc`` / ``--command``."""
    index = 0
    while index < len(rest):
        token = rest[index]
        is_c = token in {"-c", "--command"} or (
            token.startswith("-") and not token.startswith("--") and "c" in token[1:]
        )
        if is_c:
            if index + 1 >= len(rest):
                return None
            return _split_command(rest[index + 1], posix=posix)
        if token.startswith("-"):
            index += 1
            continue
        return None
    return None


def _after_cmd_c(rest: list[str], *, posix: bool) -> list[str] | None:
    """The command after ``cmd /c`` or ``cmd /k``."""
    index = 0
    while index < len(rest):
        if rest[index].casefold() in {"/c", "/k"}:
            payload = rest[index + 1:]
            if not payload:
                return None
            if len(payload) == 1:
                return _split_command(payload[0], posix=posix)
            return payload
        index += 1
    return None


def _after_powershell(rest: list[str], *, posix: bool) -> list[str] | None:
    """The command after ``powershell``/``pwsh`` ``-Command`` / ``-c``."""
    flags = {"-command", "-c", "/command", "/c"}
    index = 0
    while index < len(rest):
        if rest[index].casefold() in flags:
            payload = rest[index + 1:]
            if not payload:
                return None
            if len(payload) == 1:
                return _split_command(payload[0], posix=posix)
            return payload
        index += 1
    return None


def _after_python_m_main(tokens: list[str]) -> list[str] | None:
    """Argv after ``python -m main``. None when this is not that invocation."""
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "-c":
            return None
        if token == "-m":
            module = tokens[index + 1] if index + 1 < len(tokens) else ""
            if _exe_name(module) == "main":
                return tokens[index + 2:]
            return None
        if token in {"-W", "-X"}:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return None
    return None


def _unwrap(tokens: list[str], *, posix: bool) -> tuple[list[str], bool]:
    """Peel env prefixes and shell wrappers.

    The second value is true when the remaining tokens are already the CLI's
    argv (``python -m main``), with no ``psamvault``/``pv`` word in front.
    """
    for _ in range(_MAX_UNWRAP):
        tokens = _skip_assignments(tokens)
        if not tokens:
            return [], False
        exe = _exe_name(tokens[0])
        if exe == "env":
            tokens = _after_env(tokens)
            continue
        if exe in _SHELLS:
            inner = _after_shell_c(tokens[1:], posix=posix)
            if inner is None:
                return tokens, False
            tokens = inner
            continue
        if exe == "cmd":
            inner = _after_cmd_c(tokens[1:], posix=posix)
            if inner is None:
                return tokens, False
            tokens = inner
            continue
        if exe in _POWERSHELL:
            inner = _after_powershell(tokens[1:], posix=posix)
            if inner is None:
                return tokens, False
            tokens = inner
            continue
        if exe in _PYTHONS:
            inner = _after_python_m_main(tokens)
            if inner is None:
                return tokens, False
            return inner, True
        return tokens, False
    return tokens, False


def _positionals(tokens: list[str]) -> list[str]:
    """Drop flags. Global options (``--agent``, ``--verbose``) sit before the subcommand."""
    return [token for token in tokens if token != "--" and not token.startswith("-")]


def _match_cli(positionals: list[str]) -> str | None:
    folded = [word.casefold() for word in positionals]
    matched: tuple[str, ...] | None = None
    for path in _BLOCKED_PATHS:
        if folded[:len(path)] == list(path) and (matched is None or len(path) > len(matched)):
            matched = path
    if matched is None:
        return None
    return " ".join(matched)


def _reads_psamvault_file(tokens: list[str]) -> bool:
    for token in tokens:
        if token.startswith("-"):
            continue
        if ".psamvault" in token.casefold():
            return True
    return False


def _refusal(operation: str) -> str:
    return (
        f"Refusing to run this command: it matches a secret-revealing "
        f"psamvault operation ({operation}). "
        "run_with_credential will not reveal another vault secret. "
        "Use the MCP tools for the credential this call already holds. "
        "This blocklist is a convenience, not the security boundary. "
        f"The child is started with {_AGENT_ENV}=1, which extra_env cannot "
        "override, and the psamvault CLI reveal gate is what refuses a reveal. "
        "A command this list does not recognise still runs, and still sees "
        "that variable. Full isolation needs a separate OS user."
    )


def _is_command_blocked(command: str, *, posix: bool | None = None) -> str | None:
    """Return an error message if the command is blocked, else None.

    ``posix`` selects the quoting rules. The default follows this process:
    POSIX shlex on Unix, Windows shlex on Windows. Tests pass it explicitly
    so both rule sets run on one machine.
    """
    if posix is None:
        posix = os.name != "nt"
    tokens = _split_command(command, posix=posix)
    if not tokens:
        return None
    tokens, cli_direct = _unwrap(tokens, posix=posix)
    if not tokens:
        return None
    if cli_direct:
        matched = _match_cli(_positionals(tokens))
        return _refusal(matched) if matched else None
    exe = _exe_name(tokens[0])
    if exe in _CLI_NAMES:
        matched = _match_cli(_positionals(tokens[1:]))
        return _refusal(matched) if matched else None
    if exe in _FILE_READERS and _reads_psamvault_file(tokens[1:]):
        return _refusal(f"{exe} of a .psamvault path")
    return None


async def run_command_with_credential(
    command: str,
    credential_value: str,
    inject_as: str = "env",
    env_var_name: Optional[str] = None,
    extra_env: Optional[dict[str, str]] = None,
    workdir: Optional[str] = None,
    timeout: int = 120,
    username: str | None = None,
) -> dict:
    """Run a shell command with a credential injected via env var or stdin.

    Output is scanned for the credential and its common encodings and replaced
    with ``[REDACTED]``. That scan is defence in depth, not a boundary: a form
    it does not know about can still come back, and a command can contact a
    host that was not written in the command string.

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

    # Build environment. The session bus the server adopted (and the variables
    # GDBus would use to find that same bus) are not copied in. extra_env is
    # applied after that removal, which is the explicit pass-through.
    # PSAMVAULT_AGENT is forced further down, after extra_env and after the
    # credential assignment, so neither can clear it.
    from mcp_server.session import child_environment

    env = child_environment(extra_env)

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

    # child_environment applies extra_env after it strips the session bus and
    # the secret-shaped PSAMVAULT_* names, so a caller can put this variable
    # back as "0" or "". The credential assignment can do the same when
    # env_var_name is PSAMVAULT_AGENT. Set it last, on the dict the child
    # actually receives. The CLI treats a truthy value as an agent and the
    # default human-only policy then refuses the reveal.
    env[_AGENT_ENV] = "1"

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
    stdout_text = _redact(stdout_text, credential_value, username=username)
    stderr_text = _redact(stderr_text, credential_value, username=username)

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
                # POSIX: give the command its own session, and therefore its own process group, so the
                # timeout kill can target the command's tree without touching the caller's group. Without
                # this the child shares our group, and `killpg` (below) kills US — this process is the
                # MCP server, so a timed-out command took the server down with it. Windows kills by PID
                # tree (taskkill /T), so it needs no equivalent.
                **({"start_new_session": True} if os.name != "nt" else {}),
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


def _redact(text: str, credential_value: str, *, username: str | None = None) -> str:
    """Replace the credential, its encodings, and its reverse.

    Defence in depth only. The raw value, an 8-character prefix, the reversed
    text, base64 (standard and urlsafe, padded and unpadded), URL-encoding,
    hex, and the basic-auth ``username:value`` blob are removed. A form this
    list does not know about can still come back. Binding the destination is
    the boundary; this is the second pass.
    """
    from mcp_server.redact import redact_text

    return redact_text(text, credential_value, username=username)


def _kill_process_tree(pid: int) -> None:
    """Kill a child AND everything it spawned (best effort)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=30)
        else:
            pgid = os.getpgid(pid)
            if pgid == os.getpgrp():
                # Never signal our own group: the caller is in it (the MCP server that spawned the
                # command, or a test runner), so killpg here would kill the caller instead of the
                # command. Only reachable if the child was spawned without start_new_session, which is
                # a bug worth surviving rather than a bug worth fatal-ing on.
                os.kill(pid, signal.SIGKILL)
            else:
                os.killpg(pgid, signal.SIGKILL)
    except Exception:
        pass
