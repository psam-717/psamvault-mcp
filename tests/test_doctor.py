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
        lambda: {"version": state["pipx_version"], "apps_by_venv": state["pipx_records"], "ok": state.get("pipx_ok", True)},
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


class TestPipxUnavailableOnPath:
    """No pipx means ownership of an unclaimed bin-dir app is UNKNOWN — not "ours, therefore stale".

    Independent verification reproduced the false positive: with pipx removed from PATH, the CLI's own
    `psamvault` app was reported as a leftover of this package.
    """

    def test_no_stale_links_claimed_when_pipx_cannot_be_asked(self, fake_machine):
        fake_machine["pipx_ok"] = False
        fake_machine["pipx_version"] = None
        report = doctor.diagnose(probe_index=False)
        assert report["stale_links"] == []
        assert not any("no longer declares" in f for f in report["findings"]), report["findings"]

    def test_render_says_pipx_is_unavailable_instead_of_stale(self, fake_machine):
        fake_machine["pipx_ok"] = False
        fake_machine["pipx_version"] = None
        rendered = doctor.render(doctor.diagnose(probe_index=False))
        assert "unknown (pipx not available on PATH)" in rendered
        assert "[stale]" not in rendered

    def test_a_genuine_leftover_is_still_caught_when_pipx_answers(self, fake_machine):
        fake_machine["linked"] = ["psamvault", "psamvault-mcp", "psamvault-old"]
        report = doctor.diagnose(probe_index=False)
        assert report["stale_links"] == ["psamvault-old"]


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


class TestFixRepairsPipxsVersion:
    """`--fix` must repair what PIPX has, never the interpreter that happens to run doctor.

    Run doctor from an older sandbox — the usage this PR documents — and the naive choice would
    `pipx install --force` that older version over a working install: a silent downgrade, with
    `assume_free=True` skipping the second opinion.
    """

    def test_repairs_the_pipx_version_not_the_running_one(self, fake_machine, capsys, monkeypatch):
        fake_machine.update({"installed": "0.5.2", "pipx_venv_version": "0.5.3", "holders": []})
        calls: list[str] = []
        import mcp_server.compat as compat

        monkeypatch.setattr(
            compat, "_install",
            lambda v, force_uv=False, assume_free=False: (
                calls.append(v), {"ok": True, "installer": "pipx"})[1],
        )
        report = doctor.diagnose(probe_index=False)
        doctor._fix(report)
        assert calls == ["0.5.3"], "never downgrade the live install to the sandbox's version"

    def test_refuses_when_no_version_can_be_determined(self, fake_machine, capsys):
        fake_machine["installed"] = None
        fake_machine["pipx_venv_version"] = None
        fake_machine["holders"] = []
        report = doctor.diagnose(probe_index=False)
        assert doctor._fix(report) == 2
        assert "could not be determined" in capsys.readouterr().out


class TestTheAdvertisedUpgradeCommandCarriesTheFlag:
    """doctor's advice must be the same command compat's gate will accept."""

    def test_finding_uses_the_command_compat_decided_on(self, fake_machine, monkeypatch):
        import mcp_server.compat as compat

        monkeypatch.setattr(compat, "check", lambda **k: {
            "installed_skill": "1.9.0", "skill_floor": "1.9.0", "skill_drift": False,
            "latest_published": "0.6.0", "published_newer": "0.6.0",
            "apply_command": "psamvault-mcp compat --apply --latest --allow-breaking",
        })
        report = doctor.diagnose(probe_index=True)
        assert any("--apply --latest --allow-breaking" in f for f in report["findings"]), report["findings"]


class TestRender:
    def test_shows_the_manual_fix_when_the_venv_is_busy(self, fake_machine):
        report = doctor.diagnose(probe_index=False)
        text = doctor.render(report)
        assert "stop the gateway" in text
        assert "doctor --fix" in text
        assert "pipx install --force" not in text, (
            "the busy path must not advertise a raw recreate that bypasses doctor's own checks"
        )

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


class TestBusyPathAdviceMatchesTheRepairTarget:
    """The busy path is the one users copy — `--fix` refuses while holders exist.

    Round 1 fixed the *automated* downgrade; the printed one-liner still said
    `pipx install --force psamvault-mcp==<running version>`, which is the sandbox's version when
    doctor runs from a sandbox, and recommends a raw recreate even when the probe could not be trusted.
    """

    def test_never_pins_the_running_interpreters_version(self, fake_machine):
        fake_machine.update({"installed": "0.5.2", "pipx_venv_version": "0.5.3"})
        text = doctor.render(doctor.diagnose(probe_index=False))
        assert "==0.5.2" not in text, "the sandbox's version must never be the advertised pin"
        assert "psamvault-mcp doctor --fix" in text

    def test_uncertain_probe_does_not_recommend_recreating_the_venv(self, fake_machine):
        fake_machine.update({"holders": [], "free": False})  # probe failed -> treated as busy
        report = doctor.diagnose(probe_index=False)
        text = doctor.render(report)
        assert report["venv_probe_uncertain"] is True
        assert "pipx install --force" not in text
        assert "could not be trusted" in text

    def test_free_venv_still_points_at_fix(self, fake_machine):
        fake_machine["holders"] = []
        text = doctor.render(doctor.diagnose(probe_index=False))
        assert "doctor --fix" in text


# ── Round 3: polish & merge-bar ───────────────────────────────────────────────
class TestDoctorDoesNotAdvertiseFixForWhatItCannotRepair:
    """`--fix` reinstalls the SAME version to relink apps — it cannot apply an update or a skill fix.

    A report whose only finding is "0.6.0 is published" used to end with `fix: doctor --fix`: a wasted
    reinstall that leaves the finding, followed by the command the finding already named.
    """

    @staticmethod
    def _no_install_drift(fake_machine):
        fake_machine.update({
            "declared": ["psamvault-mcp"],
            "linked": ["psamvault", "psamvault-mcp"],
            "pipx_version": "0.5.2",
            "holders": [],
        })

    def test_update_only_report_is_not_fixable(self, fake_machine, monkeypatch):
        import mcp_server.compat as compat

        self._no_install_drift(fake_machine)
        monkeypatch.setattr(compat, "check", lambda **k: {
            "installed_skill": "1.9.0", "skill_floor": "1.9.0", "skill_drift": False,
            "latest_published": "0.6.0", "published_newer": "0.6.0",
            "apply_command": "psamvault-mcp compat --apply --latest --allow-breaking",
        })
        report = doctor.diagnose(probe_index=True)
        assert report["fixable"] is False
        text = doctor.render(report)
        assert "doctor --fix" not in text, text
        assert "nothing to relink" in text

    def test_fix_refuses_and_names_the_real_repair(self, fake_machine, monkeypatch, capsys):
        import mcp_server.compat as compat

        self._no_install_drift(fake_machine)
        monkeypatch.setattr(compat, "check", lambda **k: {
            "installed_skill": "1.9.0", "skill_floor": "1.9.0", "skill_drift": False,
            "latest_published": "0.6.0", "published_newer": "0.6.0",
            "apply_command": "psamvault-mcp compat --apply --latest --allow-breaking",
        })
        report = doctor.diagnose(probe_index=True)
        rc = doctor._fix(report)
        out = capsys.readouterr().out
        assert rc == 0  # a no-op, not a failure
        assert "psamvault-mcp compat --apply --latest --allow-breaking" in out

    def test_a_skill_only_gap_points_at_sync_skill(self, fake_machine, monkeypatch, capsys):
        import mcp_server.compat as compat

        self._no_install_drift(fake_machine)
        monkeypatch.setattr(compat, "check", lambda **k: {
            "installed_skill": "1.8.0", "skill_floor": "1.9.0", "skill_drift": True,
            "latest_published": "0.5.2", "published_newer": None, "apply_command": None,
        })
        report = doctor.diagnose(probe_index=True)
        assert report["fixable"] is False
        doctor._fix(report)
        assert "compat --sync-skill" in capsys.readouterr().out

    def test_install_drift_is_still_fixable(self, fake_machine):
        report = doctor.diagnose(probe_index=False)  # the fixture: unlinked entry point + stale records
        assert report["fixable"] is True
        assert "doctor --fix" in doctor.render(report)


def test_the_dead_pipx_version_wrapper_is_gone():
    """It was a second `pipx list --json` waiting to happen; diagnose reads the records it already has."""
    assert not hasattr(doctor, "_pipx_metadata_version")


class TestHolderReporting:
    """The refusal must say WHAT holds the venv, and the advice must fit the situation.

    Reported from a real run on 0.5.3: the gateways were already stopped, the venv was held anyway,
    and the message said "2 process(es) holding it ... stop the gateway first" — an instruction to
    repeat what had just been done, naming nothing. The holders were the desktop app's MCP server
    and a shell's wrapper, neither of which the text mentioned.
    """

    def test_refusal_names_every_holder(self, fake_machine, capsys):
        fake_machine["holders"] = [
            {"pid": 6492, "name": "python.exe", "cmdline": 'python.exe -c "from mcp_server.main import main; main()"'},
            {"pid": 7812, "name": "bash.exe", "cmdline": "source /c/… "},
        ]

        doctor._fix(doctor.diagnose(probe_index=False))

        out = capsys.readouterr().out
        assert "pid 6492 python.exe" in out and "pid 7812 bash.exe" in out

    def test_advice_points_at_the_desktop_app_for_mcp_servers(self, fake_machine, capsys):
        """ "stop the gateway" is not enough: every open desktop session holds an MCP server, and the
        app spawns a new one the moment you kill it."""
        fake_machine["holders"] = [
            {"pid": 1, "name": "python.exe", "cmdline": 'python.exe -c "from mcp_server.main import main; main()"'},
        ]

        doctor._fix(doctor.diagnose(probe_index=False))

        out = capsys.readouterr().out
        assert "gateway stop --all" in out and "desktop app" in out
        assert "stop the gateway first" not in out

    def test_render_names_the_holders_in_the_venv_row(self, fake_machine):
        fake_machine["holders"] = [{"pid": 42, "name": "python.exe", "cmdline": "…"}]

        text = doctor.render(doctor.diagnose(probe_index=False))

        assert "1 process(es) holding it: python.exe(42)" in text

    def test_render_still_marks_a_failed_probe_as_unknown(self, fake_machine):
        fake_machine["holders"] = []
        fake_machine["free"] = False

        text = doctor.render(doctor.diagnose(probe_index=False))

        assert "unknown (probe failed)" in text
