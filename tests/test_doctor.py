"""Tests for mcp_server/doctor.py — installation drift diagnosis.

The doctor exists because of one real, silent failure: pipx links a package's console scripts only
when pipx itself installs it, so an upgrade performed with `uv pip install` inside the pipx venv
leaves pipx's records stale and any newly added entry point unreachable on PATH. These tests pin the
exact shape of psam's machine (2026-09-14): installed 0.5.2, pipx records 0.4.4, `psamvault-compat`
declared but not linked, the venv held by running MCP servers.
"""

import json
from types import SimpleNamespace

import pytest

from mcp_server import doctor


@pytest.fixture
def fake_machine(monkeypatch):
    """A configurable stand-in for the machine: pipx records, entry points, links, holders."""
    state = {
        "installed": "0.5.2",
        "declared": ["psamvault-compat", "psamvault-mcp"],
        "linked": ["psamvault", "psamvault-mcp"],
        "pipx_records": {
            "psamvault": ["psamvault.exe", "pv.exe"],
            "psamvault-mcp": ["psamvault-mcp.exe"],
        },
        "pipx_version": "0.4.4",
        "holders": [{"pid": 1680, "name": "python.exe", "cmdline": "…psamvault-mcp…"}],
        "import_ok": True,
    }
    monkeypatch.setattr(doctor, "_installed_version", lambda: state["installed"])
    monkeypatch.setattr(doctor, "_declared_entry_points", lambda: list(state["declared"]))
    monkeypatch.setattr(doctor, "_fresh_import", lambda: (state["import_ok"], "0.5.2" if state["import_ok"] else "boom"))
    monkeypatch.setattr(
        doctor, "_install_env",
        lambda: SimpleNamespace(
            linked_apps=lambda: set(state["linked"]),
            holders=lambda: list(state["holders"]),
            is_free=lambda: state.get("free", not state["holders"]),
        ),
    )
    monkeypatch.setattr(
        doctor, "_pipx_records",
        lambda: {"version": state["pipx_version"], "apps_by_venv": state["pipx_records"]},
    )
    monkeypatch.setattr(doctor, "_pipx_venv_version", lambda: state.get("pipx_venv_version"))
    monkeypatch.setattr(
        "mcp_server.compat.check",
        lambda **k: {
            "installed_skill": "1.8.0", "skill_floor": "1.8.0", "skill_drift": False,
            "latest_published": state["installed"], "published_newer": None,
        },
    )
    return state


class TestDiagnoseFindsRealDrift:
    def test_reports_the_unlinked_entry_point(self, fake_machine):
        """The exact defect that made `psamvault-compat` unreachable."""
        report = doctor.diagnose(probe_index=False)
        assert report["missing_links"] == ["psamvault-compat"]
        assert any("psamvault-compat" in f and "NOT linked" in f for f in report["findings"])
        assert report["exit_code"] == 1

    def test_reports_stale_pipx_records(self, fake_machine):
        report = doctor.diagnose(probe_index=False)
        assert any("pipx records say 0.4.4" in f for f in report["findings"])

    def test_reports_venv_holders(self, fake_machine):
        report = doctor.diagnose(probe_index=False)
        assert report["venv_free"] is False
        assert any("hold the venv" in f for f in report["findings"])

    def test_does_not_flag_another_packages_app_as_stale(self, fake_machine):
        """`psamvault` belongs to the CLI's venv — not a leftover of this package."""
        report = doctor.diagnose(probe_index=False)
        assert report["stale_links"] == []

    def test_flags_a_genuine_leftover(self, fake_machine):
        """A bin-dir app that no package claims IS stale."""
        fake_machine["linked"] = ["psamvault", "psamvault-mcp", "psamvault-old"]
        report = doctor.diagnose(probe_index=False)
        assert report["stale_links"] == ["psamvault-old"]

    def test_reports_missing_and_published_update(self, fake_machine):
        monkeypatch_check = {
            "installed_skill": "1.8.0", "skill_floor": "1.8.0", "skill_drift": False,
            "latest_published": "0.6.0", "published_newer": "0.6.0",
        }
        import mcp_server.compat as compat

        original = compat.check
        compat.check = lambda **k: monkeypatch_check
        try:
            report = doctor.diagnose(probe_index=True)
        finally:
            compat.check = original
        assert any("0.6.0 is published" in f for f in report["findings"])


class TestHealthyMachine:
    def test_clean_when_everything_lines_up(self, fake_machine):
        fake_machine["declared"] = ["psamvault-mcp"]
        fake_machine["linked"] = ["psamvault", "psamvault-mcp"]
        fake_machine["pipx_version"] = "0.5.2"
        fake_machine["holders"] = []
        report = doctor.diagnose(probe_index=False)
        assert report["findings"] == []
        assert report["exit_code"] == 0
        assert "healthy" in doctor.render(report)

    def test_broken_import_is_a_finding(self, fake_machine):
        fake_machine["declared"] = ["psamvault-mcp"]
        fake_machine["linked"] = ["psamvault", "psamvault-mcp"]
        fake_machine["pipx_version"] = "0.5.2"
        fake_machine["holders"] = []
        fake_machine["import_ok"] = False
        report = doctor.diagnose(probe_index=False)
        assert any("does not import cleanly" in f for f in report["findings"])


class TestFix:
    def test_refuses_while_the_venv_is_in_use(self, fake_machine, capsys):
        """Recreating the venv would fight a running python.exe — refuse and explain."""
        report = doctor.diagnose(probe_index=False)
        rc = doctor._fix(report)
        out = capsys.readouterr().out
        assert rc == 2
        assert "refusing to repair" in out

    def test_reinstalls_when_free(self, fake_machine, capsys, monkeypatch):
        fake_machine["holders"] = []
        calls = []
        import mcp_server.compat as compat

        monkeypatch.setattr(
            compat, "_install",
            lambda v, force_uv=False, assume_free=False: (
                calls.append((v, force_uv, assume_free)), {"ok": True, "installer": "pipx"}
            )[1],
        )
        report = doctor.diagnose(probe_index=False)
        rc = doctor._fix(report)
        out = capsys.readouterr().out
        assert calls == [("0.5.2", False, True)]
        assert "reinstall psamvault-mcp==0.5.2 via pipx: ok" in out
        assert rc in (0, 1)  # re-diagnoses afterwards; the fixture keeps the drift


class TestNotRunningInsideThePipxVenv:
    """Doctor run from a sandbox/other venv must not compare pipx's records with ITS OWN version."""

    def test_records_are_compared_against_the_pipx_venv_not_this_interpreter(self, fake_machine):
        fake_machine["installed"] = "0.5.3"        # the interpreter running doctor (a sandbox)
        fake_machine["pipx_version"] = "0.5.3"     # pipx's records, for the pipx venv
        fake_machine["pipx_venv_version"] = "0.5.2"  # what the pipx venv actually has
        report = doctor.diagnose(probe_index=False)
        assert report["pipx_venv_version"] == "0.5.2"
        assert any("pipx records say 0.5.3 but 0.5.2 is installed" in f for f in report["findings"]), report["findings"]
        assert "this interpreter is a different install" in doctor.render(report)

    def test_no_finding_when_records_match_the_pipx_venv(self, fake_machine):
        fake_machine["installed"] = "0.5.3"
        fake_machine["pipx_version"] = "0.5.2"
        fake_machine["pipx_venv_version"] = "0.5.2"
        report = doctor.diagnose(probe_index=False)
        assert not any("pipx records say" in f for f in report["findings"]), report["findings"]


class TestFailedProbeIsNotFree:
    """A probe that cannot answer must read as BUSY — the safe direction.

    `holders()` returns [] both for "nothing is running" and "the probe failed" (no powershell/pgrep,
    timeout, unparseable output). Deciding `--fix` on `not holders` would let pipx recreate the venv
    underneath a live server — the exact failure this module exists to prevent.
    """

    def test_probe_failure_reports_unknown_not_free(self, fake_machine):
        fake_machine["holders"] = []
        fake_machine["free"] = False  # probe failed, despite naming no holder
        report = doctor.diagnose(probe_index=False)
        assert report["venv_free"] is False
        assert report["venv_probe_uncertain"] is True
        assert any("could not confirm" in f for f in report["findings"])
        assert "unknown (probe failed)" in doctor.render(report)

    def test_fix_refuses_when_the_probe_failed(self, fake_machine, capsys):
        fake_machine["holders"] = []
        fake_machine["free"] = False
        report = doctor.diagnose(probe_index=False)
        rc = doctor._fix(report)
        assert rc == 2
        assert "refusing to repair" in capsys.readouterr().out


class TestRender:
    def test_shows_the_manual_fix_when_the_venv_is_busy(self, fake_machine):
        report = doctor.diagnose(probe_index=False)
        text = doctor.render(report)
        assert "stop the gateway" in text
        assert "pipx install --force psamvault-mcp==0.5.2" in text

    def test_shows_the_fix_command_when_free(self, fake_machine):
        fake_machine["holders"] = []
        report = doctor.diagnose(probe_index=False)
        assert "psamvault-mcp doctor --fix" in doctor.render(report)


class TestMain:
    def test_json_output_is_machine_readable(self, fake_machine, capsys):
        rc = doctor.main(["--json", "--no-index"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["exit_code"] == rc
        assert payload["missing_links"] == ["psamvault-compat"]
