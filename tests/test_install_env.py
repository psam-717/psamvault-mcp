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


def _entry(pid, name, cmdline):
    """One Win32_Process row, as ConvertTo-Json renders it."""
    return {"ProcessId": pid, "Name": name, "CommandLine": cmdline}


# ── discovery ──────────────────────────────────────────────────────────────────
def test_venv_dir_honours_the_env_override(monkeypatch, tmp_path):
    _as_windows(monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setenv(ie.VENV_ENV, str(tmp_path / "elsewhere"))
    assert ie.venv_dir() == tmp_path / "elsewhere"


def test_venv_dir_windows_default_is_the_pipx_venvs_root(monkeypatch, tmp_path):
    _as_windows(monkeypatch)
    monkeypatch.delenv(ie.VENV_ENV, raising=False)
    local = tmp_path / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    # note the doubled "pipx/pipx": pipx's own venvs live under its app dir
    assert ie.venv_dir() == local / "pipx" / "pipx" / "venvs" / "psamvault-mcp"


def test_venv_dir_posix_default(monkeypatch, tmp_path):
    _as_posix(monkeypatch)
    monkeypatch.delenv(ie.VENV_ENV, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert ie.venv_dir() == tmp_path / ".local" / "pipx" / "venvs" / "psamvault-mcp"


def test_venv_dir_windows_without_localappdata_keeps_compat_fallback(monkeypatch, tmp_path):
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
    """The probe's OWN command line carries the venv path (it is the where-clause), so a naive
    query matches itself — verified on Windows 11. $PID must stay in the filter."""
    _as_windows(monkeypatch)
    _use_venv(monkeypatch, _venv(tmp_path))
    fake = _patch(monkeypatch, _Run(stdout=""))

    ie.holders()

    assert fake.calls[-1][0] in {"powershell", "pwsh"}
    assert "-NoProfile" in fake.calls[-1] and "-NonInteractive" in fake.calls[-1]
    assert "Get-CimInstance Win32_Process" in fake.script
    assert "$_.ProcessId -ne $PID" in fake.script
    assert "$_.CommandLine -like" in fake.script
    assert "ConvertTo-Json -InputObject $hits -Compress" in fake.script
    # The @() wrapper is what guarantees JSON output even when NOTHING matched. Without it an empty
    # pipeline prints nothing at all, and "no process is running" becomes indistinguishable from
    # "the probe died" — the ambiguity that let a held venv read as free.
    assert "$hits = @(" in fake.script
    assert str(ie.venv_dir()) in fake.script, "the venv must be the match, not every process"
    # coarse on purpose: a command line launched with forward slashes has no backslash to match,
    # so the clause asks for both spellings (the precise filter runs on the parse side)
    assert f"'*{ie.venv_dir()}*'" in fake.script
    assert f"'*{str(ie.venv_dir()).replace(chr(92), '/')}*'" in fake.script
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


def test_windows_awkward_path_is_escaped_for_the_like_clause(monkeypatch, tmp_path):
    """A quote or a bracket in the path must not break the query, and an unescaped '[' would start
    a PowerShell character class and silently stop matching (i.e. report a busy venv as free)."""
    _as_windows(monkeypatch)
    _use_venv(monkeypatch, tmp_path / "we'ird [x86]" / "psamvault-mcp")
    fake = _patch(monkeypatch, _Run(stdout=""))

    ie.holders()

    assert "we''ird" in fake.script, "'' is a literal quote inside a PowerShell single-quoted string"
    assert "[[]x86]" in fake.script, "a bare '[' would be read as a character class"
    assert fake.script.count("'*") == 2 and fake.script.count("*'") == 2, "both spellings, once each"


def test_ps_like_literal_escapes_quotes_and_brackets():
    assert ie._ps_like_literal("a'b") == "a''b"
    assert ie._ps_like_literal("C:\\Program Files [x86]") == "C:\\Program Files [[]x86]"
    assert ie._ps_like_literal("no specials") == "no specials"


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
    """The compat path can ask about a venv other than the default one."""
    _as_windows(monkeypatch)
    elsewhere = tmp_path / "other" / "bin" / "python"
    fake = _patch(monkeypatch, _Run(stdout=json.dumps(
        _entry(7, "python", f'"{elsewhere}" -m mcp_server.main')
    )))

    assert [holder["pid"] for holder in ie.holders(elsewhere)] == [7]
    assert str(elsewhere.parent.parent) in fake.script


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
