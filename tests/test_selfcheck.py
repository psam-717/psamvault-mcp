"""Tests for selfcheck.py — installed vs served psamvault-mcp version.

The subprocess layer is always faked here: no test spawns a server, and no test touches PyPI
(an autouse fixture stubs the published-version lookup). Real deadline behaviour is exercised
through the ``_spawn_child`` seam with a fake child, so the timeout path is covered for real.
"""

from __future__ import annotations

import io
import json
import sys
from types import SimpleNamespace

import pytest

from mcp_server import selfcheck


# ── fakes ──────────────────────────────────────────────────────────────────────
class FakeChild:
    """Minimal stand-in for a Popen'd MCP server. Never answers anything."""

    def __init__(self, stdout_text: str = "", stderr_text: str = ""):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(stdout_text)
        self.stderr = io.StringIO(stderr_text)
        self.killed = False
        self.returncode: int | None = None

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True


def serving_child(version: str, tools: list[str] | None = None, compatibility: dict | None = None) -> FakeChild:
    """A fake child that answers initialize -> tools/list -> get_version correctly."""
    tools = tools if tools is not None else ["get_version", "search_vault_tools"]
    frames = [
        {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": name} for name in tools]}},
        {"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", "text": json.dumps(
            {"version": version, "compatibility": compatibility or {}})}]}},
    ]
    return FakeChild(stdout_text="".join(json.dumps(f) + "\n" for f in frames))


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch):
    """Guarantee the published-version lookup never reaches PyPI, whatever the parent module does."""
    monkeypatch.setattr(selfcheck, "_load_latest_published", lambda: (lambda: None))


@pytest.fixture
def stub_python(monkeypatch: pytest.MonkeyPatch):
    """Resolver returns a real file path (sys.executable) so main() does not bail with exit 2."""
    monkeypatch.setattr(selfcheck, "resolve_python", lambda explicit=None: (sys.executable, "test"))


def stub_installed(monkeypatch: pytest.MonkeyPatch, value: str | None, error: str | None = None):
    monkeypatch.setattr(selfcheck, "probe_installed", lambda python, timeout=None: (value, error))


def stub_served(monkeypatch: pytest.MonkeyPatch, value: str, **kwargs):
    monkeypatch.setattr(
        selfcheck, "probe_served",
        lambda python, timeout=None: {"python": python, "version": value, "tool_count": 13,
                                      "tools": kwargs.get("tools", []),
                                      "compatibility": kwargs.get("compatibility", {}),
                                      "stderr": "", "error": kwargs.get("error")},
    )


# ── (a) installed == served ────────────────────────────────────────────────────
class TestAgreement:
    def test_same_version_is_ok(self, monkeypatch, stub_python, capsys):
        stub_installed(monkeypatch, "0.5.2")
        stub_served(monkeypatch, "0.5.2")
        assert selfcheck.main([]) == 0

    def test_ok_output_reports_both_numbers(self, monkeypatch, stub_python, capsys):
        stub_installed(monkeypatch, "0.5.2")
        stub_served(monkeypatch, "0.5.2")
        selfcheck.main([])
        out = capsys.readouterr().out
        assert "installed           : 0.5.2" in out
        assert "a NEW session serves: 0.5.2" in out
        assert "result: OK" in out
        assert "PROBLEMS" not in out

    def test_expect_match_is_ok(self, monkeypatch, stub_python, capsys):
        stub_installed(monkeypatch, "0.5.2")
        stub_served(monkeypatch, "0.5.2")
        assert selfcheck.main(["--expect", "0.5.2"]) == 0

    def test_json_report_shape(self, monkeypatch, stub_python, capsys):
        stub_installed(monkeypatch, "0.5.2")
        stub_served(monkeypatch, "0.5.2")
        selfcheck.main(["--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["installed_version"] == "0.5.2"
        assert payload["served_version"] == "0.5.2"
        assert payload["failures"] == []


# ── (b) installed != served ────────────────────────────────────────────────────
class TestMismatch:
    def test_newer_installed_than_served_fails(self, monkeypatch, stub_python, capsys):
        stub_installed(monkeypatch, "0.5.3")
        stub_served(monkeypatch, "0.5.2")
        assert selfcheck.main([]) == 1
        out = capsys.readouterr().out
        assert "PROBLEMS:" in out
        assert "installed 0.5.3 but a fresh process serves 0.5.2" in out
        assert "result: FAIL" in out

    def test_mismatch_is_ok_when_no_expect_and_equal(self, monkeypatch, stub_python):
        """Control: the mismatch check must not fire on equal versions."""
        stub_installed(monkeypatch, "0.5.3")
        stub_served(monkeypatch, "0.5.3")
        assert selfcheck.main([]) == 0

    def test_unreadable_installed_version_fails(self, monkeypatch, stub_python, capsys):
        stub_installed(monkeypatch, None, error="psamvault-mcp is not installed in that venv")
        stub_served(monkeypatch, "0.5.2")
        assert selfcheck.main([]) == 1
        assert "could not read the installed version" in capsys.readouterr().out


# ── (c) --expect mismatch ──────────────────────────────────────────────────────
class TestExpect:
    def test_expect_mismatch_fails_even_when_installed_matches_served(self, monkeypatch, stub_python, capsys):
        stub_installed(monkeypatch, "0.5.2")
        stub_served(monkeypatch, "0.5.2")
        assert selfcheck.main(["--expect", "0.5.3"]) == 1
        out = capsys.readouterr().out
        assert "served 0.5.2, expected 0.5.3" in out

    def test_expect_mismatch_exit_code_in_json(self, monkeypatch, stub_python, capsys):
        stub_installed(monkeypatch, "0.5.2")
        stub_served(monkeypatch, "0.5.2")
        assert selfcheck.main(["--expect", "0.6.0", "--json"]) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert any("expected 0.6.0" in f for f in payload["failures"])


# ── (d) a child that never responds ────────────────────────────────────────────
class TestDeadlines:
    def test_silent_child_times_out_and_fails(self, monkeypatch, stub_python, capsys):
        child = FakeChild(stdout_text="")  # never writes a frame
        monkeypatch.setattr(selfcheck, "_spawn_child", lambda argv, cwd: child)
        monkeypatch.setattr(selfcheck, "CHILD_TIMEOUT_S", 0.3)
        stub_installed(monkeypatch, "0.5.2")

        assert selfcheck.main([]) == 1
        out = capsys.readouterr().out
        assert "timed out" in out
        assert "result: FAIL" in out
        assert child.killed is True  # the finally block must always reap the child

    def test_child_that_dies_is_reported_with_stderr(self, monkeypatch, stub_python, capsys):
        child = FakeChild(stderr_text="Traceback: boom\n")
        child.returncode = 1
        monkeypatch.setattr(selfcheck, "_spawn_child", lambda argv, cwd: child)
        monkeypatch.setattr(selfcheck, "CHILD_TIMEOUT_S", 0.3)
        stub_installed(monkeypatch, "0.5.2")

        assert selfcheck.main([]) == 1
        out = capsys.readouterr().out
        assert "exited (code 1)" in out
        assert "boom" in out

    def test_real_probe_uses_the_fake_child_and_reads_the_version(self, monkeypatch):
        """Exercises the real probe_served: threads, queue, framing, parsing and the kill()."""
        child = serving_child("0.5.2", tools=["get_version", "a", "b"],
                              compatibility={"newest_release_skill_version": "0.5.2",
                                             "tool_surface_matches_newest": True})
        monkeypatch.setattr(selfcheck, "_spawn_child", lambda argv, cwd: child)
        result = selfcheck.probe_served("python", timeout=5)
        assert result["error"] is None
        assert result["version"] == "0.5.2"
        assert result["tool_count"] == 3
        assert result["tools"] == ["a", "b", "get_version"]
        assert result["compatibility"]["tool_surface_matches_newest"] is True
        assert child.killed is True

    def test_real_probe_reports_unreadable_get_version(self, monkeypatch):
        child = FakeChild(stdout_text=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": {}}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 3,
                          "result": {"content": [{"type": "text", "text": "not json"}]}}) + "\n")
        monkeypatch.setattr(selfcheck, "_spawn_child", lambda argv, cwd: child)
        result = selfcheck.probe_served("python", timeout=5)
        assert result["error"] and "get_version" in result["error"]
        assert "version" not in result
        assert child.killed is True


# ── (e) no interpreter ─────────────────────────────────────────────────────────
class TestInterpreterResolution:
    def test_the_running_interpreter_wins_when_it_has_psamvault_mcp(self, monkeypatch, tmp_path):
        """Run inside a sandbox/venv and the answer must be about THAT install, not the pipx one."""
        monkeypatch.setattr(selfcheck, "_installed_version_of", lambda python: "0.5.3")
        monkeypatch.setattr(selfcheck, "candidate_pythons", lambda: pytest.fail(
            "the pipx venv must not be consulted when the running env is itself an install"))
        path, why = selfcheck.resolve_python()
        assert path == sys.executable
        assert "this interpreter" in why and "0.5.3" in why

    def test_missing_venv_python_exits_2(self, monkeypatch, capsys):
        monkeypatch.setattr(selfcheck, "resolve_python", lambda explicit=None: (
            None, "no psamvault-mcp venv python found (tried: C:/nope/python.exe)"))
        assert selfcheck.main([]) == 2
        assert "no psamvault-mcp venv python found" in capsys.readouterr().err

    def test_missing_venv_python_exits_2_in_json(self, monkeypatch, capsys):
        monkeypatch.setattr(selfcheck, "resolve_python", lambda explicit=None: (None, "nothing found"))
        assert selfcheck.main(["--json"]) == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False and payload["exit_code"] == 2

    def test_explicit_missing_python_exits_2_without_falling_back(self, tmp_path, capsys):
        """A wrong --python is an error, not a silent switch to some other interpreter."""
        missing = tmp_path / "nope" / "python.exe"
        assert selfcheck.main(["--python", str(missing)]) == 2
        assert "does not exist" in capsys.readouterr().err

    def test_resolve_prefers_install_env_when_it_exists(self, monkeypatch, tmp_path):
        monkeypatch.setattr(selfcheck, "_installed_version_of", lambda python: None)
        fake = tmp_path / "python.exe"
        fake.write_text("")
        monkeypatch.setattr(selfcheck, "_from_install_env", lambda: str(fake))
        monkeypatch.setattr(selfcheck, "_pipx_env_value", lambda: None)
        path, why = selfcheck.resolve_python()
        assert path == str(fake) and why == "mcp_server.install_env.venv_python"

    def test_install_env_absent_falls_back(self, monkeypatch, tmp_path):
        monkeypatch.setattr(selfcheck, "_installed_version_of", lambda python: None)
        """mcp_server/install_env.py does not exist yet — resolution must still work."""
        fake = tmp_path / "python.exe"
        fake.write_text("")
        monkeypatch.setattr(selfcheck, "_from_install_env", lambda: None)
        monkeypatch.setattr(selfcheck, "_pipx_env_value", lambda: str(fake))
        path, why = selfcheck.resolve_python()
        assert path == str(fake) and "pipx" in why

    @staticmethod
    def _swap_install_env(monkeypatch, module):
        """Replace the install_env module so the swap is visible whichever import form is used.

        ``from mcp_server.install_env import venv_python`` resolves through sys.modules, while
        ``from mcp_server import install_env`` resolves the PACKAGE ATTRIBUTE first (which another
        module may already have bound to the real module). Patch both.
        """
        import mcp_server

        monkeypatch.setitem(sys.modules, "mcp_server.install_env", module)
        monkeypatch.setattr(mcp_server, "install_env", module, raising=False)

    def test_real_install_env_module_is_used_when_present(self, monkeypatch, tmp_path):
        """Integration: whatever the shipped install_env names becomes the first candidate."""
        fake = tmp_path / "python.exe"
        fake.write_text("")
        self._swap_install_env(monkeypatch, SimpleNamespace(venv_python=lambda: fake))
        assert selfcheck._from_install_env() == str(fake)

    def test_install_env_pointing_at_missing_venv_falls_through(self, monkeypatch, tmp_path):
        monkeypatch.setattr(selfcheck, "_installed_version_of", lambda python: None)
        """venv_python() may name a venv that has not been created yet — keep looking."""
        real = tmp_path / "python.exe"
        real.write_text("")
        monkeypatch.setattr(selfcheck, "_from_install_env", lambda: str(tmp_path / "gone" / "python.exe"))
        monkeypatch.setattr(selfcheck, "_pipx_env_value", lambda: str(real))
        path, why = selfcheck.resolve_python()
        assert path == str(real) and "pipx" in why

    def test_from_install_env_tolerates_a_bad_module(self, monkeypatch):
        """Any shape of install_env (non-callable, raising, arity mismatch, empty) -> None."""
        bad_shapes = [
            SimpleNamespace(venv_python=""),  # non-callable, empty
            SimpleNamespace(venv_python=lambda: (_ for _ in ()).throw(RuntimeError("nope"))),
            SimpleNamespace(venv_python=lambda a, b: a),  # arity mismatch
            SimpleNamespace(),  # attribute missing
        ]
        for bad in bad_shapes:
            self._swap_install_env(monkeypatch, bad)
            assert selfcheck._from_install_env() is None, bad


# ── cwd hygiene, optional published lookup, secrecy ────────────────────────────
class TestNeutralCwd:
    def test_neutral_cwd_is_never_the_checkout(self):
        cwd = selfcheck.neutral_cwd()
        assert not selfcheck._is_inside(cwd, selfcheck.repo_root())
        assert cwd.rstrip("/\\") != str(selfcheck.repo_root())

    def test_installed_probe_runs_outside_the_checkout(self, monkeypatch):
        """The metadata read must not run where the local package shadows site-packages."""
        seen: dict = {}

        def fake_run(argv, cwd, timeout):
            seen["cwd"] = cwd
            seen["argv"] = argv
            return SimpleNamespace(returncode=0, stdout='{"version": "0.5.2"}', stderr="")

        monkeypatch.setattr(selfcheck, "_run_child", fake_run)
        version, error = selfcheck.probe_installed("C:/venv/python.exe")
        assert (version, error) == ("0.5.2", None)
        assert not selfcheck._is_inside(seen["cwd"], selfcheck.repo_root())

    def test_installed_probe_surfaces_a_broken_venv(self, monkeypatch):
        monkeypatch.setattr(selfcheck, "_run_child", lambda argv, cwd, timeout: SimpleNamespace(
            returncode=1, stdout="", stderr="No package metadata was found for psamvault-mcp"))
        version, error = selfcheck.probe_installed("C:/venv/python.exe")
        assert version is None and "No package metadata" in error

    def test_traceback_stderr_collapses_to_one_line(self, monkeypatch, stub_python, capsys):
        """A whole traceback must not smear across the PROBLEMS list — the last line is the cause."""
        stub_installed(monkeypatch, None, error="StopIteration")
        stub_served(monkeypatch, "0.5.2")
        assert selfcheck.main([]) == 1
        problems = capsys.readouterr().out.split("PROBLEMS:")[1]
        bullets = [line for line in problems.splitlines() if line.strip().startswith("- ")]
        assert bullets == ["  - could not read the installed version: StopIteration"]

    def test_error_line_takes_the_last_real_line(self):
        assert selfcheck._error_line("Traceback:\n  File x, line 1\nStopIteration\n\n") == "StopIteration"
        assert selfcheck._error_line("") == "the interpreter exited non-zero"
        assert selfcheck._error_line(None) == "the interpreter exited non-zero"


class TestPublishedVersionIsOptional:
    def test_missing_lookup_is_unknown_not_a_failure(self, monkeypatch, stub_python, capsys):
        monkeypatch.setattr(selfcheck, "_load_latest_published",
                            lambda: (_ for _ in ()).throw(ImportError("no latest_published")))
        stub_installed(monkeypatch, "0.5.2")
        stub_served(monkeypatch, "0.5.2")
        assert selfcheck.main([]) == 0
        assert "unknown" in capsys.readouterr().out

    def test_raising_lookup_is_unknown_not_a_failure(self, monkeypatch, stub_python, capsys):
        def boom():
            raise RuntimeError("network down")

        monkeypatch.setattr(selfcheck, "_load_latest_published", lambda: boom)
        stub_installed(monkeypatch, "0.5.2")
        stub_served(monkeypatch, "0.5.2")
        assert selfcheck.main([]) == 0
        out = capsys.readouterr().out
        assert "unknown" in out and "RuntimeError" in out

    def test_published_version_has_no_failure_path(self, monkeypatch):
        monkeypatch.setattr(selfcheck, "_load_latest_published", lambda: (_ for _ in ()).throw(ImportError()))
        assert selfcheck.published_version() == (None, "unknown (this build has no latest_published lookup)")

    def test_no_network_flag_skips_the_lookup(self, monkeypatch, stub_python, capsys):
        called: list[int] = []
        monkeypatch.setattr(selfcheck, "published_version", lambda: (called.append(1), ("0.9.9", None))[1])
        stub_installed(monkeypatch, "0.5.2")
        stub_served(monkeypatch, "0.5.2")
        assert selfcheck.main(["--no-network"]) == 0
        assert called == []
        assert "not checked" in capsys.readouterr().out


class TestOutputHygiene:
    def test_report_never_contains_vault_material(self, monkeypatch, stub_python, capsys):
        monkeypatch.setenv("PSAMVAULT_ACCESS_TOKEN", "super-secret-token")
        stub_installed(monkeypatch, "0.5.2")
        stub_served(monkeypatch, "0.5.2")
        selfcheck.main(["--json"])
        out = capsys.readouterr().out
        assert "super-secret-token" not in out
        assert "password" not in out.lower()
        assert "vek" not in out.lower()

    def test_compatibility_block_is_reported(self, monkeypatch, stub_python, capsys):
        stub_installed(monkeypatch, "0.5.2")
        stub_served(monkeypatch, "0.5.2", compatibility={
            "newest_release_skill_version": "0.5.2", "paired_skill_version": "0.5.2",
            "tool_surface_matches_newest": True, "breaking_pending": False, "expected_tool_count": 13,
        })
        assert selfcheck.main([]) == 0
        out = capsys.readouterr().out
        assert "skill floor         : 0.5.2" in out
        assert "tools match contract: True" in out
