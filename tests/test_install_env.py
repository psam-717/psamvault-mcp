"""Tests for mcp_server/install_env.py — venv discovery and holder detection.

Why this is pinned so hard: ``psamvault-compat`` may only run ``pipx install --force`` (which
replaces the whole venv) when nothing holds the venv's ``python.exe``; otherwise it must fall back
to a safe in-venv ``uv pip install``. A probe that wrongly says "free" destroys the venv under a
running server, so **fail-busy** is tested as carefully as the happy paths.

Nothing here touches a real process table or a real pipx: every probe is a monkeypatched
``subprocess.run``. The fake POSIX/probe paths are built with ``str(Path(...))`` rather than
hard-coded forward slashes, so these tests exercise the parsers and not the test host's path
flavour.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest

from mcp_server import compat
from mcp_server import install_env as ie


# ── fakes ──────────────────────────────────────────────────────────────────────
class _Run:
    """Stands in for subprocess.run: records the argv/kwargs and returns canned output."""

    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "", raises=None):
        self.stdout, self.returncode, self.stderr, self.raises = stdout, returncode, stderr, raises
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        self.kwargs.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)

    @property
    def script(self) -> str:
        """The PowerShell script (the last argv element of the -Command form)."""
        return self.calls[-1][-1]


def _patch(monkeypatch, fake: _Run) -> _Run:
    monkeypatch.setattr(ie.subprocess, "run", fake)
    return fake


class _OSShim:
    """``os`` as install_env sees it, with only ``name`` swapped.

    Patching the real ``os.name`` globally is not an option: on Windows, pytest's own internals then
    build a PosixPath for the cwd and die with NotImplementedError (which aborts the whole run).
    Everything but ``name`` is proxied to the real module, so environ/getpid still behave.
    """

    def __init__(self, name: str):
        self._name = name

    def __getattr__(self, attribute):
        return getattr(os, attribute)

    @property
    def name(self) -> str:
        return self._name


def _as_windows(monkeypatch):
    monkeypatch.setattr(ie, "os", _OSShim("nt"))


def _as_posix(monkeypatch):
    monkeypatch.setattr(ie, "os", _OSShim("posix"))


def _venv(tmp_path: Path, name: str = "psamvault-mcp") -> Path:
    return tmp_path / "pipx" / "venvs" / name


def _use_venv(monkeypatch, venv: Path) -> Path:
    monkeypatch.setenv(ie.VENV_ENV, str(venv))
    return venv


def _entry(pid, name, cmdline, ppid=None):
    """One Win32_Process row, as ConvertTo-Json renders it.

    ``ppid`` is omitted unless given: the probe always asks for ParentProcessId, but the callers that
    do not care exercise the documented degradation (a table without links ends every ancestor walk).
    """
    row = {"ProcessId": pid, "Name": name, "CommandLine": cmdline}
    if ppid is not None:
        row["ParentProcessId"] = ppid
    return row


# ── discovery ──────────────────────────────────────────────────────────────────
def test_venv_dir_honours_the_env_override(monkeypatch, tmp_path):
    _as_windows(monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setenv(ie.VENV_ENV, str(tmp_path / "elsewhere"))
    assert ie.venv_dir() == tmp_path / "elsewhere"


def test_venv_dir_windows_default_is_the_pipx_venvs_root(monkeypatch, tmp_path):
    monkeypatch.setattr(ie, "_pipx_env_path", lambda name: None)  # exercise the fallback
    _as_windows(monkeypatch)
    monkeypatch.delenv(ie.VENV_ENV, raising=False)
    local = tmp_path / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    # note the doubled "pipx/pipx": pipx's own venvs live under its app dir
    assert ie.venv_dir() == local / "pipx" / "pipx" / "venvs" / "psamvault-mcp"


def test_venv_dir_posix_default(monkeypatch, tmp_path):
    monkeypatch.setattr(ie, "_pipx_env_path", lambda name: None)  # exercise the fallback
    _as_posix(monkeypatch)
    monkeypatch.delenv(ie.VENV_ENV, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert ie.venv_dir() == tmp_path / ".local" / "pipx" / "venvs" / "psamvault-mcp"


def test_venv_dir_windows_without_localappdata_keeps_compat_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(ie, "_pipx_env_path", lambda name: None)  # exercise the fallback
    """compat.pipx_python() falls back to ~/.local when LOCALAPPDATA is unset; so do we, rather
    than inventing a third location for the same install."""
    _as_windows(monkeypatch)
    monkeypatch.delenv(ie.VENV_ENV, raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert ie.venv_dir() == tmp_path / ".local" / "pipx" / "venvs" / "psamvault-mcp"


def test_venv_python_is_scripts_pythonexe_on_windows(monkeypatch, tmp_path):
    _as_windows(monkeypatch)
    _use_venv(monkeypatch, _venv(tmp_path))
    assert ie.venv_python() == ie.venv_dir() / "Scripts" / "python.exe"


def test_venv_python_is_bin_python_on_posix(monkeypatch, tmp_path):
    _as_posix(monkeypatch)
    _use_venv(monkeypatch, _venv(tmp_path))
    assert ie.venv_python() == ie.venv_dir() / "bin" / "python"


def test_venv_python_agrees_with_compat_pipx_python(monkeypatch, tmp_path):
    """The two modules must never disagree about which interpreter 'the install' targets —
    install_env only ADDS the holder probe and the bin-dir facts on top of compat's path."""
    _as_windows(monkeypatch)
    monkeypatch.delenv(ie.VENV_ENV, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    assert ie.venv_python() == compat.pipx_python()


# ── Windows holder probe ───────────────────────────────────────────────────────
def test_windows_probe_command_asks_cim_for_json_and_excludes_itself(monkeypatch, tmp_path):
    """The probe's OWN command line carries the venv path (it is the pattern), so a naive query
    matches itself — verified on Windows 11. $PID must stay in the filter."""
    _as_windows(monkeypatch)
    _use_venv(monkeypatch, _venv(tmp_path))
    fake = _patch(monkeypatch, _Run(stdout=""))

    ie.holders()

    assert fake.calls[-1][0] in {"powershell", "pwsh"}
    assert "-NoProfile" in fake.calls[-1] and "-NonInteractive" in fake.calls[-1]
    assert "Get-CimInstance Win32_Process" in fake.script
    assert "$_.ProcessId -ne $PID" in fake.script
    assert "ConvertTo-Json -InputObject $hits -Compress" in fake.script
    # The script enumerates ONLY — there is no '-like' clause, so exactly one rule decides what
    # counts (the Python _venv_matcher). A PowerShell clause had to be told both separator spellings
    # by hand, and a backslash-only one silently missed real holders.
    assert "-like" not in fake.script
    # Parent links ride along: they are what lets the caller drop this command's own launcher chain
    # (the entry-point shim), which otherwise counted as a holder of the venv it was asking about.
    assert "ParentProcessId" in fake.script
    # The @() wrapper is what guarantees JSON output even when NOTHING matched. Without it an empty
    # pipeline prints nothing at all, and "no process is running" becomes indistinguishable from
    # "the probe died" — the ambiguity that let a held venv read as free.
    assert "$hits = @(" in fake.script
    assert 5 <= fake.kwargs[-1]["timeout"] <= 30, "PowerShell cold start is slow but bounded"


def test_windows_holders_parse_a_list_payload(monkeypatch, tmp_path):
    """Several matches => a JSON array. Also pins the exact-match filter: the -like wildcard admits
    a venv whose name is a PREFIX of this one ('psamvault-mcp-old'), which is not our venv."""
    _as_windows(monkeypatch)
    venv = _use_venv(monkeypatch, _venv(tmp_path))
    py = ie.venv_python()
    other = str(tmp_path / "pipx" / "venvs" / "psamvault-mcp-old" / "Scripts" / "python.exe")
    payload = json.dumps([
        _entry(8808, "python.exe", f'"{py}" -c "import mcp_server"'),
        _entry(1680, "python.exe", f'"{py}" -m mcp_server.main --serve'),
        _entry(4242, "explorer.exe", "C:\\Windows\\explorer.exe"),
        _entry(5150, "python.exe", f'"{other}" -m mcp_server.main'),
    ])
    _patch(monkeypatch, _Run(stdout=payload))

    found = ie.holders()

    assert [holder["pid"] for holder in found] == [1680, 8808], "sorted, only real holders"
    assert found[1]["name"] == "python.exe"
    assert "-m mcp_server.main" in found[0]["cmdline"]


def test_windows_counts_a_holder_launched_with_forward_slashes(monkeypatch, tmp_path):
    """Found live: a process started as ``D:/…/.venv/Scripts/python.exe`` has NO backslash in its
    command line, so a where-clause that demands '<venv>\\' misses it. Missing a holder means
    reporting a busy venv as free — the dangerous direction — so both separator forms count, and the
    prefix filter still rejects a different venv."""
    _as_windows(monkeypatch)
    venv = _use_venv(monkeypatch, _venv(tmp_path))
    slashed = str(venv).replace("\\", "/")
    _patch(monkeypatch, _Run(stdout=json.dumps([
        _entry(1680, "python.exe", f"{slashed}/Scripts/python.exe -m mcp_server.main"),
        _entry(2200, "python.exe", f'{venv}\\Scripts/python.exe -c "import mcp_server"'),
        _entry(5150, "python.exe", f"{slashed}-old/Scripts/python.exe -m mcp_server.main"),
    ])))

    assert [holder["pid"] for holder in ie.holders()] == [1680, 2200]


def test_venv_matcher_is_separator_agnostic_case_insensitive_and_prefix_safe(tmp_path):
    venv = tmp_path / "pipx" / "venvs" / "psamvault-mcp"
    matcher = ie._venv_matcher(venv)

    assert matcher.search(str(venv) + "\\Scripts\\python.exe")
    assert matcher.search(str(venv).replace("\\", "/") + "/Scripts/python.exe")
    assert matcher.search(str(venv).upper() + "\\Scripts\\python.exe"), "Windows paths fold case"
    assert matcher.search(str(venv) + "-old\\Scripts\\python.exe") is None
    assert matcher.search(str(venv)) is None, "the bare path with no separator is not a command line"


def test_windows_holders_parse_a_bare_object_for_a_single_match(monkeypatch, tmp_path):
    """Get-CimInstance hands ConvertTo-Json ONE object for one match and an array for several."""
    _as_windows(monkeypatch)
    _use_venv(monkeypatch, _venv(tmp_path))
    py = ie.venv_python()
    _patch(monkeypatch, _Run(stdout=json.dumps(_entry(1680, "python.exe", f'"{py}" -m mcp_server.main'))))

    assert ie.holders() == [
        {"pid": 1680, "name": "python.exe", "cmdline": f'"{py}" -m mcp_server.main'}
    ]


def test_windows_no_matches_is_an_empty_list_and_a_free_venv(monkeypatch, tmp_path):
    """No match prints `[]` — a successful probe, so the venv is free.

    The script wraps the pipeline in ``@(...)`` and uses ``-InputObject`` specifically so that an empty
    result is still JSON. A bare ``| ConvertTo-Json`` prints nothing at all when nothing matched, which
    made "no process holds the venv" indistinguishable from "the probe died" (see
    TestAnUnreadableProbeIsNeverFree).
    """
    _as_windows(monkeypatch)
    _use_venv(monkeypatch, _venv(tmp_path))
    _patch(monkeypatch, _Run(stdout="[]"))

    assert ie.holders() == []
    assert ie.is_free() is True


def test_windows_null_payload_is_an_empty_list(monkeypatch, tmp_path):
    _as_windows(monkeypatch)
    _use_venv(monkeypatch, _venv(tmp_path))
    _patch(monkeypatch, _Run(stdout="null"))

    assert ie.holders() == []


def test_windows_holder_rows_without_our_path_are_dropped(monkeypatch, tmp_path):
    """Belt and braces on the parse side: a row that survived the where-clause but does not carry
    the venv path (and rows with an unusable pid) never become 'holders'."""
    _as_windows(monkeypatch)
    _use_venv(monkeypatch, _venv(tmp_path))
    _patch(monkeypatch, _Run(stdout=json.dumps([
        _entry(11, "python.exe", "C:\\Python311\\python.exe -m http.server"),
        _entry("not-a-pid", "python.exe", f'"{ie.venv_python()}" -m mcp_server.main'),
        "not even an object",
    ])))

    assert ie.holders() == []


def test_windows_holders_never_report_the_process_asking(monkeypatch, tmp_path):
    """A psamvault-compat entry point runs from INSIDE the venv it is asking about; counting itself
    would report every venv as busy forever."""
    _as_windows(monkeypatch)
    _use_venv(monkeypatch, _venv(tmp_path))
    _patch(monkeypatch, _Run(stdout=json.dumps([
        _entry(os.getpid(), "python.exe", f'"{ie.venv_python()}" -m mcp_server.compat --check'),
        _entry(1680, "python.exe", f'"{ie.venv_python()}" -m mcp_server.main'),
    ])))

    assert [holder["pid"] for holder in ie.holders()] == [1680]


def test_windows_awkward_path_still_matches(monkeypatch, tmp_path):
    """A quote or a bracket in the path must not break matching. This used to be the PowerShell
    clause's problem — an unescaped ``[`` starts a character class and silently stops matching, i.e.
    reports a busy venv as free. The escaping burden moved with the match: it now happens in
    :func:`_venv_matcher` via ``re.escape``."""
    _as_windows(monkeypatch)
    _use_venv(monkeypatch, tmp_path / "we'ird [x86]" / "psamvault-mcp")
    py = ie.venv_python()
    _patch(monkeypatch, _Run(stdout=json.dumps([
        _entry(1680, "python.exe", f'"{py}" -m mcp_server.main'),
    ])))

    assert [holder["pid"] for holder in ie.holders()] == [1680]


# ── this command is not a holder ───────────────────────────────────────────────
def test_windows_own_launcher_shim_is_not_a_holder(monkeypatch, tmp_path):
    """``psamvault-mcp doctor --fix`` runs as entry-point shim -> python -> probe, and the SHIM is a
    process of its own whose command line carries the venv path.

    Found live on 0.5.3: the shim was counted, so the total could never reach zero and ``--fix``
    refused on every machine — including one whose venv nothing else touched. Excluding the running
    interpreter is not enough; the launcher above it has to go too.
    """
    _as_windows(monkeypatch)
    _use_venv(monkeypatch, _venv(tmp_path))
    py = ie.venv_python()
    shim = str(ie.venv_dir() / "Scripts" / "psamvault-mcp.exe")
    monkeypatch.setattr(ie.os, "getpid", lambda: 5000)
    _patch(monkeypatch, _Run(stdout=json.dumps([
        _entry(5000, "python.exe", f'"{py}" -c "doctor"', ppid=1000),
        _entry(1000, "psamvault-mcp.exe", f'"{shim}" doctor --fix', ppid=900),
        _entry(2000, "python.exe", f'"{py}" -c "from mcp_server.main import main; main()"', ppid=1),
    ])))

    assert [holder["pid"] for holder in ie.holders()] == [2000], "the shim is us, not a holder"


def test_windows_launcher_chain_drops_every_contiguous_wrapper(monkeypatch, tmp_path):
    """A shell that inlined the command keeps the venv path in ITS command line too, so the walk must
    keep going up — and stop at the first ancestor that does not carry the venv, so an unrelated
    process is never hidden. (A genuine holder further down the tree must survive both.)"""
    _as_windows(monkeypatch)
    _use_venv(monkeypatch, _venv(tmp_path))
    py = ie.venv_python()
    shim = str(ie.venv_dir() / "Scripts" / "psamvault-mcp.exe")
    bash = f'bash -c \'source /c/…; "{shim}" doctor --fix\''
    monkeypatch.setattr(ie.os, "getpid", lambda: 5000)
    _patch(monkeypatch, _Run(stdout=json.dumps([
        _entry(5000, "python.exe", f'"{py}" -c "doctor"', ppid=1000),
        _entry(1000, "psamvault-mcp.exe", f'"{shim}" doctor --fix', ppid=900),
        _entry(900, "bash.exe", bash, ppid=800),
        _entry(800, "explorer.exe", "C:\\Windows\\explorer.exe", ppid=1),  # no venv -> walk stops
        _entry(2000, "python.exe", f'"{py}" -c "from mcp_server.main import main; main()"', ppid=1),
    ])))

    assert [holder["pid"] for holder in ie.holders()] == [2000]


def test_windows_launcher_drop_degrades_without_parent_links(tmp_path):
    """A listing with no ParentProcessId cannot be walked. That degrades to the old behaviour (self
    excluded, launcher kept) instead of to a wrong answer — a missed exclusion is a repair that
    refuses, never a venv replaced under a live server."""
    venv = _venv(tmp_path)
    payload = [_entry(1000, "psamvault-mcp.exe", f'"{venv}\\Scripts\\psamvault-mcp.exe" doctor --fix')]

    assert [holder["pid"] for holder in ie._parse_windows(payload, venv, parent_of=None)] == [1000]


def test_windows_powershell_falls_back_to_pwsh(monkeypatch, tmp_path):
    """A host with only PowerShell 7 must still be probed, not silently reported as busy."""
    _as_windows(monkeypatch)
    _use_venv(monkeypatch, _venv(tmp_path))
    py = ie.venv_python()
    seen: list[str] = []

    def fake(cmd, **kwargs):
        seen.append(cmd[0])
        if cmd[0] == "powershell":
            raise FileNotFoundError("no powershell on this host")
        return subprocess.CompletedProcess(cmd, 0, json.dumps(_entry(1680, "python.exe", f'"{py}"')), "")

    monkeypatch.setattr(ie.subprocess, "run", fake)

    assert [holder["pid"] for holder in ie.holders()] == [1680]
    assert seen == ["powershell", "pwsh"]


@pytest.mark.parametrize(
    "raises,returncode,stdout",
    [
        (FileNotFoundError("no powershell"), 0, ""),
        (subprocess.TimeoutExpired("powershell", 15), 0, ""),
        (None, 1, ""),
        (None, 0, "not json at all"),
        (None, 0, '"just a string"'),
        (None, 0, "42"),
    ],
)
def test_windows_probe_failures_degrade_to_no_holders_and_a_busy_venv(
    monkeypatch, tmp_path, raises, returncode, stdout
):
    """Every failure mode: empty list from holders(), False from is_free() — never an exception."""
    _as_windows(monkeypatch)
    _use_venv(monkeypatch, _venv(tmp_path))
    _patch(monkeypatch, _Run(stdout=stdout, returncode=returncode, raises=raises))

    assert ie.holders() == []
    assert ie.is_free() is False, "an unanswerable probe must never claim the venv is free"


# ── POSIX holder probe ─────────────────────────────────────────────────────────
def test_posix_probe_uses_pgrep_full_command_line(monkeypatch, tmp_path):
    _as_posix(monkeypatch)
    _use_venv(monkeypatch, _venv(tmp_path))
    fake = _patch(monkeypatch, _Run(stdout=""))

    ie.holders()

    argv = fake.calls[-1]
    assert argv[0] == "pgrep"
    assert "-fl" in argv, "-f matches the command line, the only place the venv path appears"
    assert str(ie.venv_python()) in argv
    assert 5 <= fake.kwargs[-1]["timeout"] <= 30


def test_posix_holders_parse_pgrep_output(monkeypatch, tmp_path):
    _as_posix(monkeypatch)
    venv = _use_venv(monkeypatch, _venv(tmp_path))
    py = ie.venv_python()
    stdout = "\n".join([
        f"8808 {py} -c import mcp_server",
        # a non-holder whose line pgrep could surface (pattern is a regex) — must be dropped
        f"4242 /usr/bin/vim {venv}/pyproject.toml",
        f"1680 {py} -m mcp_server.main",
        f"{os.getpid()} {py} -m mcp_server.compat --check",
    ])
    _patch(monkeypatch, _Run(stdout=stdout))

    found = ie.holders()

    assert [holder["pid"] for holder in found] == [1680, 8808]
    assert found[0]["name"] == Path(str(py)).name
    assert found[0]["cmdline"].endswith("-m mcp_server.main")


def test_posix_pgrep_exit_1_means_no_match_not_failure(monkeypatch, tmp_path):
    """pgrep exits 1 when nothing matched and 2+ on a real error; only the latter is a failure."""
    _as_posix(monkeypatch)
    _use_venv(monkeypatch, _venv(tmp_path))
    _patch(monkeypatch, _Run(returncode=1))

    assert ie.holders() == []
    assert ie.is_free() is True


@pytest.mark.parametrize(
    "raises,returncode,stdout",
    [
        (FileNotFoundError("no pgrep"), 0, ""),
        (subprocess.TimeoutExpired("pgrep", 15), 0, ""),
        (None, 3, "pgrep: something went wrong"),
        (None, 2, ""),
    ],
)
def test_posix_probe_failures_degrade_to_no_holders_and_a_busy_venv(
    monkeypatch, tmp_path, raises, returncode, stdout
):
    _as_posix(monkeypatch)
    _use_venv(monkeypatch, _venv(tmp_path))
    _patch(monkeypatch, _Run(stdout=stdout, returncode=returncode, raises=raises))

    assert ie.holders() == []
    assert ie.is_free() is False


def test_holders_accepts_an_explicit_interpreter(monkeypatch, tmp_path):
    """The compat path can ask about a venv other than the default one.

    The pattern used to travel INSIDE the script (the where-clause), which is what pinned this; now
    the script only enumerates and the match happens in :func:`_parse_windows`, so the behaviour is
    what gets pinned: rows from another venv are not holders of this interpreter.
    """
    _as_windows(monkeypatch)
    elsewhere = tmp_path / "other" / "bin" / "python"
    _patch(monkeypatch, _Run(stdout=json.dumps([
        _entry(7, "python", f'"{elsewhere}" -m mcp_server.main'),
        _entry(9, "python", f'"{ie.venv_python()}" -m mcp_server.main'),
    ])))

    assert [holder["pid"] for holder in ie.holders(elsewhere)] == [7]


# ── describe ───────────────────────────────────────────────────────────────────
def test_describe_with_no_holders():
    assert ie.describe([]) == "no processes hold the venv"


def test_describe_with_one_holder():
    line = ie.describe([{"pid": 1680, "name": "python.exe", "cmdline": "x"}])
    assert line == "1 process holds the venv: python.exe(1680)"


def test_describe_with_many_holders_lists_every_one():
    line = ie.describe([
        {"pid": 1680, "name": "python.exe", "cmdline": "x"},
        {"pid": 8808, "name": "python.exe", "cmdline": "y"},
        {"pid": 9100, "name": "python.exe", "cmdline": "z"},
    ])
    assert line == "3 processes hold the venv: python.exe(1680), python.exe(8808), python.exe(9100)"


def test_describe_tolerates_a_row_without_a_name():
    assert ie.describe([{"pid": 42}]) == "1 process holds the venv: unknown(42)"


# ── pipx bin dir ───────────────────────────────────────────────────────────────
def test_bin_dir_prefers_pipxs_own_answer(monkeypatch, tmp_path):
    """PIPX_BIN_DIR can be moved; pipx knows where it actually linked the shims."""
    target = tmp_path / "shims"
    fake = _patch(monkeypatch, _Run(stdout=f"{target}\r\n"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))

    assert ie.bin_dir() == target
    assert fake.calls[-1] == ["pipx", "environment", "--value", "PIPX_BIN_DIR"]


def test_bin_dir_windows_fallback_when_pipx_is_absent(monkeypatch, tmp_path):
    _as_windows(monkeypatch)
    profile = tmp_path / "profile"
    monkeypatch.setenv("USERPROFILE", str(profile))
    _patch(monkeypatch, _Run(raises=FileNotFoundError("no pipx")))

    assert ie.bin_dir() == profile / ".local" / "bin"


def test_bin_dir_posix_fallback_when_pipx_is_absent(monkeypatch, tmp_path):
    _as_posix(monkeypatch)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _patch(monkeypatch, _Run(raises=FileNotFoundError("no pipx")))

    assert ie.bin_dir() == tmp_path / ".local" / "bin"


@pytest.mark.parametrize("stdout,returncode", [("", 0), ("\r\n", 0), ("None", 0), ("null", 0), ("whatever", 1)])
def test_bin_dir_falls_back_when_pipx_answers_nothing_usable(monkeypatch, tmp_path, stdout, returncode):
    _as_windows(monkeypatch)
    profile = tmp_path / "profile"
    monkeypatch.setenv("USERPROFILE", str(profile))
    _patch(monkeypatch, _Run(stdout=stdout, returncode=returncode))

    assert ie.bin_dir() == profile / ".local" / "bin"


def test_bin_dir_is_none_when_there_is_no_home_either(monkeypatch):
    """The declared ``Path | None`` must be reachable: no pipx and no home means 'do not know'."""
    _as_windows(monkeypatch)
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.setattr(Path, "home", lambda: (_ for _ in ()).throw(RuntimeError("no home")))
    _patch(monkeypatch, _Run(raises=FileNotFoundError("no pipx")))

    assert ie.bin_dir() is None


# ── entry points pipx linked ───────────────────────────────────────────────────
def test_linked_apps_strips_the_exe_suffix_and_filters_the_prefix(monkeypatch, tmp_path):
    bindir = tmp_path / "bin"
    (bindir / "sub").mkdir(parents=True)
    for name in ("psamvault-mcp.exe", "psamvault-compat.exe", "psamvault.exe"):
        (bindir / name).write_text("", encoding="utf-8")
    (bindir / "pipx.exe").write_text("", encoding="utf-8")
    (bindir / "psamvault-mcp-data").mkdir()  # a directory is not a linked app
    monkeypatch.setattr(ie, "bin_dir", lambda: bindir)

    assert ie.linked_apps() == {"psamvault-mcp", "psamvault-compat", "psamvault"}
    assert ie.linked_apps(prefix="psamvault-c") == {"psamvault-compat"}


def test_linked_apps_from_real_pipx_output(monkeypatch, tmp_path):
    bindir = tmp_path / "shims"
    bindir.mkdir()
    (bindir / "psamvault-mcp.exe").write_text("", encoding="utf-8")
    _patch(monkeypatch, _Run(stdout=f"{bindir}\n"))

    assert ie.linked_apps() == {"psamvault-mcp"}


def test_linked_apps_is_empty_when_the_bin_dir_is_unknown_or_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(ie, "bin_dir", lambda: None)
    assert ie.linked_apps() == set()

    monkeypatch.setattr(ie, "bin_dir", lambda: tmp_path / "never-created")
    assert ie.linked_apps() == set()


# ── a probe that cannot answer must never read as "free" ──────────────────────
class TestAnUnreadableProbeIsNeverFree:
    """Independent verification found the dangerous direction of a decode failure.

    On Windows the probe child writes the OEM console codepage (437 here) while the parent decodes
    UTF-8, so ONE non-ASCII byte anywhere in a matched command line empties ``stdout``. ``[]`` ("nothing
    is running") and ``""`` ("the probe died") were then indistinguishable, and ``is_free()`` — whose
    entire contract is *fails busy* — returned True. That flips ``--apply`` onto
    ``pipx install --force``, which recreates the venv while a live server holds it: the one failure
    this module exists to prevent. Same shape on POSIX: ``pgrep`` exit 0 with empty stdout.
    """

    def test_powershell_with_no_output_is_a_failure_not_an_empty_venv(self, monkeypatch):
        fake = _patch(monkeypatch, _Run(stdout="", returncode=0))
        with pytest.raises(RuntimeError, match="no output"):
            ie._probe_windows(Path(ie.venv_python()))
        assert fake.calls, "the probe must have actually run"

    def test_that_failure_makes_is_free_fail_busy(self, monkeypatch):
        _patch(monkeypatch, _Run(stdout="", returncode=0))
        monkeypatch.setattr(ie, "_is_windows", lambda: True)
        assert ie.is_free() is False
        monkeypatch.setattr(ie, "_is_windows", lambda: False)
        assert ie.is_free() is False

    def test_an_empty_list_is_still_a_real_no_matches_answer(self, monkeypatch):
        """`[]` is what the script prints when nothing matched — that must stay a result, not a failure."""
        _patch(monkeypatch, _Run(stdout="[]", returncode=0))
        assert ie._probe_windows(Path(ie.venv_python())) == []

    def test_powershell_is_told_to_emit_utf8_and_decoded_leniently(self, monkeypatch):
        fake = _patch(monkeypatch, _Run(stdout="[]", returncode=0))
        ie._probe_windows(Path(ie.venv_python()))
        assert fake.kwargs[-1].get("encoding") == "utf-8"
        assert fake.kwargs[-1].get("errors") == "replace"
        assert fake.script.startswith(ie._PS_FORCE_UTF8), "the child must be forced to emit UTF-8"

    def test_pgrep_exit_0_with_no_output_is_a_failure(self, monkeypatch):
        _patch(monkeypatch, _Run(stdout="", returncode=0))
        with pytest.raises(RuntimeError, match="produced no output"):
            ie._probe_posix(Path("/x/venv/bin/python"))

    def test_pgrep_exit_1_is_still_no_match(self, monkeypatch):
        _patch(monkeypatch, _Run(stdout="", returncode=1))
        assert ie._probe_posix(Path("/x/venv/bin/python")) == []


class TestVenvDiscoveryAsksPipx:
    """`venv_dir()` must ask pipx where its venvs live, the way `bin_dir()` asks for PIPX_BIN_DIR.

    A relocated pipx home otherwise leaves the holder probe watching a ghost venv (usually "free"),
    uv writing the wheel into that ghost, and `pipx install --force` hitting the real one — three
    components, three different interpreters.
    """

    def test_relocated_venvs_root_is_used(self, monkeypatch, tmp_path):
        custom = tmp_path / "custom-venvs"
        _patch(monkeypatch, _Run(stdout=str(custom) + "\n"))
        monkeypatch.delenv(ie.VENV_ENV, raising=False)
        assert ie.venv_dir() == custom / ie.PACKAGE
        assert str(ie.venv_python()).startswith(str(custom / ie.PACKAGE))

    def test_env_override_still_wins(self, monkeypatch, tmp_path):
        _patch(monkeypatch, _Run(stdout="D:/should/not/be/used\n"))
        explicit = tmp_path / "explicit"
        monkeypatch.setenv(ie.VENV_ENV, str(explicit))
        assert ie.venv_dir() == explicit

    def test_pipx_absent_falls_back_to_the_documented_default(self, monkeypatch):
        _patch(monkeypatch, _Run(raises=FileNotFoundError("no pipx")))
        monkeypatch.delenv(ie.VENV_ENV, raising=False)
        assert ie.PACKAGE in str(ie.venv_dir())

    def test_an_empty_answer_is_not_a_path(self, monkeypatch):
        """`none`/NULL/blank from pipx must fall through, never become a directory named 'none'."""
        _patch(monkeypatch, _Run(stdout="none\n"))
        monkeypatch.delenv(ie.VENV_ENV, raising=False)
        assert ie.venv_dir().name == ie.PACKAGE
        assert "none" not in ie.venv_dir().parts
