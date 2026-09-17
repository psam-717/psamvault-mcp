"""Tests for the `psamvault-mcp <subcommand>` dispatch in mcp_server/main.py.

Two contracts live here, and both are easy to break silently:

* **The no-argument path is the MCP server.** Dispatch only fires for a known first argument, so
  Hermes' launch line (`python -c "from mcp_server.main import main; main()"`) keeps working. An
  unknown first argument must keep the historical behaviour too — it falls through to the server,
  unchanged from `main`.
* **The half-linked recovery hint must be actionable.** Printing `compat --apply --latest` there is a
  dead end (refused when the target is newer than this install's contract, a no-op when it is
  current), and an upgrade does not relink a venv whose entry points are missing. The hint therefore
  offers `doctor --fix` first and a command the flag gate accepts.
"""

import sys

import pytest

from mcp_server import main as mcp_main


def _fail_submodule(monkeypatch, dotted: str) -> None:
    """Make `from <dotted> import ...` raise, the way a half-linked install does."""
    monkeypatch.setitem(sys.modules, dotted, None)  # None in sys.modules -> ImportError


class TestDispatch:
    def test_only_known_first_arguments_dispatch(self, monkeypatch):
        calls: list[tuple[str, list[str]]] = []
        monkeypatch.setattr(
            mcp_main, "_run_subcommand",
            lambda name, argv: (calls.append((name, argv)), 0)[1],
        )
        with pytest.raises(SystemExit) as exit_info:
            monkeypatch.setattr(sys, "argv", ["psamvault-mcp", "compat", "--check"])
            mcp_main.main()
        assert exit_info.value.code == 0
        assert calls == [("compat", ["--check"])]

    def test_a_subcommand_owns_its_own_help(self, monkeypatch, capsys):
        """`compat --help` must show compat's help — dispatch runs before the -h scan."""
        monkeypatch.setattr(sys, "argv", ["psamvault-mcp", "compat", "--help"])
        with pytest.raises(SystemExit) as exit_info:
            mcp_main.main()
        assert exit_info.value.code == 0
        out = capsys.readouterr().out
        assert "psamvault-mcp compat" in out, out

    def test_unknown_first_argument_is_not_dispatch(self, monkeypatch):
        """Unchanged from main: it must NOT be treated as a subcommand."""
        seen: list[str] = []

        async def fake_server() -> None:  # _run_server is a coroutine function
            return None

        monkeypatch.setattr(mcp_main, "_run_subcommand", lambda name, argv: seen.append(name) or 0)
        monkeypatch.setattr(mcp_main, "_run_server", fake_server)
        monkeypatch.setattr(sys, "argv", ["psamvault-mcp", "bogus"])
        mcp_main.main()
        assert seen == [], "an unknown first argument must fall through, not dispatch"


class TestHalfLinkedRecoveryHint:
    def test_offers_relink_and_a_command_the_gate_accepts(self, monkeypatch, capsys):
        """selfcheck missing, compat present: the hint must offer both repairs."""
        _fail_submodule(monkeypatch, "mcp_server.selfcheck")
        rc = mcp_main._run_subcommand("selfcheck", [])
        err = capsys.readouterr().err
        assert rc == 2
        assert "doctor --fix" in err, "a same-version relink is what actually repairs a missing module"
        assert "compat --apply --latest --allow-breaking" in err, err

    def test_falls_back_when_compat_itself_is_unavailable(self, monkeypatch, capsys):
        """The half-linked case where the apply helper cannot be imported either."""
        _fail_submodule(monkeypatch, "mcp_server.compat")
        rc = mcp_main._run_subcommand("compat", [])
        err = capsys.readouterr().err
        assert rc == 2
        assert "doctor --fix" in err
        assert "compat --check" in err, "point at the surface that prints the exact command"
