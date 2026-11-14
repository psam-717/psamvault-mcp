"""run_with_credential must not be a way to print a different vault secret.

The child used to be started without ``PSAMVAULT_AGENT``. The CLI's caller
classifier then reports ``uncertain`` (no TTY, no marker), and the default
``human-only`` reveal mode allows that caller. The scrubber only removes the
credential this call injected, so ``pv get other`` would print the other
secret.

These tests use a fake ``pv`` and a throwaway value. They do not touch a vault.
"""
from __future__ import annotations

import os
import stat
import sys

import pytest

from mcp_server.cmd_runner import _is_command_blocked, run_command_with_credential

_BOUNDARY = "convenience, not the security boundary"

# Spellings that quote the same way under POSIX and Windows shlex.
_BLOCKED = [
    "psamvault get other",
    "pv get other",
    "psamvault.exe get other",
    "PV.EXE get other",
    "PSAMVAULT GET other",
    '"psamvault" get other',
    "psamvault --agent get other",
    "pv --verbose --agent get --copy other",
    "psamvault vault get other",
    "pv vault get --copy other",
    "psamvault ak-get other",
    "pv ak get other",
    "psamvault note-get other",
    "pv note get other",
    "psamvault export",
    "pv export --plaintext",
    "psamvault export -p",
    "psamvault dashboard",
    "pv dashboard --port 8500",
    "psamvault generate",
    "pv vault generate --length 32",
    "psamvault generate-codes",
    "pv recovery generate-codes",
    "psamvault backup create",
    "pv backup create --no-upload --out ./kit.json",
    "psamvault backup rotate",
    "pv backup rotate --out ./kit.json",
    "psamvault list",
    "pv vault list",
    "psamvault search query",
    "pv vault search query",
    "FOO=bar psamvault get other",
    "FOO=bar BAR=baz pv get other",
    "env pv get other",
    "env -i pv get other",
    "env -u PATH pv get other",
    "env -u PSAMVAULT_AGENT pv get other",
    "env PSAMVAULT_AGENT=0 pv get other",
    "/usr/bin/env pv get other",
    'bash -c "pv get other"',
    'bash -lc "pv ak-get other"',
    "cmd /c pv get other",
    'cmd.exe /c "psamvault get other"',
    'powershell -Command "pv get other"',
    'powershell.exe -c "psamvault ak get other"',
    'pwsh -Command "pv export --plaintext"',
    "python -m main get other",
    "python3 -m main vault get other",
    "python -E -m main get other",
    "py -m main note-get other",
    "py -3 -m main generate-codes",
    "/usr/bin/psamvault get other",
    '"C:/Program Files/psamvault.exe" get other',
    "cat ~/.psamvault/session.json",
    "cat -A file.psamvault",
    "type file.psamvault",
    'bash -c "cat foo.psamvault"',
    "env cat ~/.psamvault/config.env",
    'powershell -Command "type file.psamvault"',
]

_POSIX_ONLY_BLOCKED = [
    "'pv' get other",
    "sh -c 'psamvault get other'",
    "bash -c \"sh -c 'pv note get other'\"",
    "dash -c 'pv list'",
]

_WINDOWS_ONLY_BLOCKED = [
    '"C:\\Program Files\\psamvault.exe" get other',
    "type C:\\Users\\me\\.psamvault\\session.json",
    "cmd /C pv vault get other",
]

_ALLOWED = [
    "twine upload dist/*",
    "gh pr list",
    'git commit -m "psamvault get docs"',
    "echo psamvault get docs",
    "psamvault whoami",
    "psamvault login",
    "pv backup status",
    "psamvault backup verify",
    "psamvault ak-list",
    "pv ak list",
    "psamvault site-list",
    "psamvault note-list",
    "pv note list",
    "psamvault changelog show 0.3.0",
    "psamvault policy hosts",
    'python -c "print(1)"',
    "python -m http.server",
    "python -m main --help",
    "python -m main",
    'bash -c "twine upload dist/*"',
    "cmd /c gh pr list",
    "env twine upload dist/*",
    "type psamvault",
    "cat notes.txt",
]


def _assert_refused(command: str, *, posix: bool) -> None:
    message = _is_command_blocked(command, posix=posix)
    assert message is not None, command
    assert _BOUNDARY in message
    assert "PSAMVAULT_AGENT=1" in message
    assert "separate OS user" in message


@pytest.mark.parametrize("command", _BLOCKED + _POSIX_ONLY_BLOCKED)
def test_posix_reveal_spellings_are_refused(command):
    _assert_refused(command, posix=True)


@pytest.mark.parametrize("command", _BLOCKED + _WINDOWS_ONLY_BLOCKED)
def test_windows_reveal_spellings_are_refused(command):
    _assert_refused(command, posix=False)


@pytest.mark.parametrize("command", _ALLOWED)
@pytest.mark.parametrize("posix", [True, False])
def test_ordinary_commands_are_not_refused(command, posix):
    assert _is_command_blocked(command, posix=posix) is None, command


def test_host_quoting_refuses_a_quoted_pv_get():
    """The default follows this process, so a quoted name is not a gap on either OS."""
    command = "'pv' get other" if os.name != "nt" else '"pv" get other'
    assert _is_command_blocked(command) is not None


def test_command_substitution_is_not_a_string_the_list_understands():
    """``$(echo pv) get`` is the documented limit of the string check."""
    assert _is_command_blocked("$(echo pv) get x") is None
    assert _is_command_blocked("$(echo pv) get x", posix=False) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    ["0", "", "false", "no", "off", "true"],
)
async def test_child_sees_agent_marker_even_when_extra_env_sets_it(tmp_path, override):
    script = tmp_path / "show_agent.py"
    script.write_text(
        "import os\n"
        "print('AGENT=' + repr(os.environ.get('PSAMVAULT_AGENT')))\n"
        "print('OTHER=' + os.environ.get('TWINE_REPOSITORY_URL', ''))\n",
        encoding="utf-8",
    )
    result = await run_command_with_credential(
        command=f'"{sys.executable}" "{script}"',
        credential_value="not-a-real-secret",
        inject_as="env",
        env_var_name="TWINE_PASSWORD",
        extra_env={
            "PSAMVAULT_AGENT": override,
            "TWINE_REPOSITORY_URL": "https://upload.pypi.org/legacy/",
        },
        timeout=30,
    )
    assert result["exit_code"] == 0, result
    assert "AGENT='1'" in result["stdout"]
    assert "OTHER=https://upload.pypi.org/legacy/" in result["stdout"]
    assert "not-a-real-secret" not in result["stdout"]


@pytest.mark.asyncio
async def test_parent_agent_marker_and_the_credential_name_cannot_clear_it(monkeypatch, tmp_path):
    """A value inherited from this process, or injected under that name, is replaced."""
    monkeypatch.setenv("PSAMVAULT_AGENT", "0")
    script = tmp_path / "show_agent.py"
    script.write_text(
        "import os\n"
        "print('AGENT=' + repr(os.environ.get('PSAMVAULT_AGENT')))\n",
        encoding="utf-8",
    )
    result = await run_command_with_credential(
        command=f'"{sys.executable}" "{script}"',
        credential_value="0",
        inject_as="env",
        env_var_name="PSAMVAULT_AGENT",
        extra_env={"PSAMVAULT_AGENT": ""},
        timeout=30,
    )
    assert result["exit_code"] == 0, result
    assert "AGENT='1'" in result["stdout"]
    assert os.environ["PSAMVAULT_AGENT"] == "0"


@pytest.mark.asyncio
async def test_blocked_command_does_not_run(tmp_path):
    marker = tmp_path / "ran"
    result = await run_command_with_credential(
        command=f'pv get other && echo ran > "{marker}"',
        credential_value="not-a-real-secret",
        inject_as="env",
        env_var_name="TWINE_PASSWORD",
        timeout=30,
    )
    assert result["exit_code"] == -1
    assert _BOUNDARY in result["error"]
    assert _BOUNDARY in result["stderr"]
    assert result["stdout"] == ""
    assert not marker.exists()


@pytest.mark.asyncio
async def test_a_commit_message_that_mentions_get_still_runs(tmp_path):
    script = tmp_path / "ok.py"
    script.write_text("print('uploaded')\n", encoding="utf-8")
    result = await run_command_with_credential(
        command=f'"{sys.executable}" "{script}"',
        credential_value="not-a-real-secret",
        inject_as="stdin",
        timeout=30,
    )
    assert result["exit_code"] == 0, result
    assert "uploaded" in result["stdout"]
    assert _is_command_blocked('git commit -m "psamvault get docs"') is None
    assert _is_command_blocked("twine upload dist/*") is None
    assert _is_command_blocked("gh pr list") is None


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX command substitution")
async def test_substitution_bypass_still_marks_the_child_as_an_agent(tmp_path):
    """The string check lets ``$(echo pv) get`` through. The child still sees the marker."""
    pv = tmp_path / "pv"
    pv.write_text(
        "#!/bin/sh\nprintf 'AGENT=%s\\n' \"$PSAMVAULT_AGENT\"\n",
        encoding="utf-8",
    )
    pv.chmod(pv.stat().st_mode | stat.S_IEXEC)
    path = str(tmp_path) + os.pathsep + os.environ.get("PATH", "")
    result = await run_command_with_credential(
        command="$(echo pv) get x",
        credential_value="not-a-real-secret",
        inject_as="env",
        env_var_name="TWINE_PASSWORD",
        extra_env={"PATH": path, "PSAMVAULT_AGENT": "0"},
        timeout=30,
    )
    assert result.get("error") is None, result
    assert result["exit_code"] == 0, result
    assert "AGENT=1" in result["stdout"]
    assert "not-a-real-secret" not in result["stdout"]
