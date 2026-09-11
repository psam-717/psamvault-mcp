"""Tests for the cron detector script (silence when in sync, a report when not).

A cron detector that talks when nothing is wrong is worse than no detector: the agent job wakes,
spends tokens, and reports noise. Just as important, the script must ALWAYS exit 0 — the cron engine
treats a non-zero exit as a script FAILURE, while the change-detection signal is stdout, so an exit 1
on drift marks a healthy detector as broken.
"""

import importlib.util
import os as _os
import subprocess
import sys

_sys_path = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
sys.path.insert(0, _sys_path)

from pathlib import Path

SCRIPT = Path(_sys_path) / "scripts" / "psamvault-compat-check.py"
_spec = importlib.util.spec_from_file_location("psamvault_compat_check", SCRIPT)
detector = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(detector)


def _fake_probe(stdout: str):
    return lambda python_exe: subprocess.CompletedProcess([python_exe], 0, stdout, "")


def test_silent_when_in_sync(monkeypatch, capsys):
    monkeypatch.setattr(detector.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(detector, "run", _fake_probe('{"exit_code": 0, "in_sync": true}'))
    assert detector.main() == 0
    assert capsys.readouterr().out == "", "an in-sync pair must produce no output at all"


def test_reports_drift_with_the_apply_command(monkeypatch, capsys):
    monkeypatch.setattr(detector.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(detector, "run", _fake_probe(
        '{"exit_code": 1, "installed_mcp": "0.4.6", "target_mcp": "0.5.0", "installed_skill": "1.2.0",'
        ' "expected_skill": "1.4.0", "tool_drift": {"missing": [], "unexpected": []},'
        ' "breaking_pending": true, "findings": ["x"]}'
    ))
    assert detector.main() == 0, "the exit code must stay 0 — stdout is the signal, not the status"
    out = capsys.readouterr().out
    assert "DRIFT" in out and "0.5.0" in out
    assert "--allow-breaking" in out, "a breaking target must be flagged for the human"


def test_unparseable_output_is_reported_not_crashed(monkeypatch, capsys):
    monkeypatch.setattr(detector.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(detector, "run", _fake_probe("not json at all"))
    assert detector.main() == 0
    assert "unparseable" in capsys.readouterr().out


def test_no_interpreter_found_is_reported(monkeypatch, capsys):
    monkeypatch.setattr(detector.os.path, "isfile", lambda path: False)
    assert detector.main() == 0
    assert "no python found" in capsys.readouterr().out


def test_drift_report_names_both_remedies(monkeypatch, capsys):
    """A skill below its floor is fixed by --sync-skill; suggesting an MCP install would DOWNGRADE a
    runtime that carries unreleased work, so the report must offer both, clearly."""
    monkeypatch.setattr(detector.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(detector, "run", _fake_probe(
        '{"exit_code": 1, "installed_mcp": "0.5.1", "target_mcp": "0.5.1", "installed_skill": "1.4.0",'
        ' "skill_floor": "1.6.0", "skill_ahead": false, "tool_drift": {"missing": [], "unexpected": []},'
        ' "breaking_pending": false, "findings": ["skill version \'1.4.0\' is BELOW the floor 1.6.0"]}'
    ))
    assert detector.main() == 0
    out = capsys.readouterr().out
    assert '"psamvault_compat": "DRIFT"' in out
    assert '"skill_floor": "1.6.0"' in out
    assert "psamvault-compat --sync-skill" in out
    assert "psamvault-compat --apply" in out


def test_skill_floor_falls_back_to_the_expected_skill_key(monkeypatch, capsys):
    """Older compat builds only report `expected_skill`; the detector must still populate the floor."""
    monkeypatch.setattr(detector.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(detector, "run", _fake_probe(
        '{"exit_code": 1, "installed_mcp": "0.4.6", "target_mcp": "0.5.0", "installed_skill": "1.2.0",'
        ' "expected_skill": "1.4.0", "tool_drift": {"missing": [], "unexpected": []},'
        ' "breaking_pending": false, "findings": ["x"]}'
    ))
    assert detector.main() == 0
    assert '"skill_floor": "1.4.0"' in capsys.readouterr().out
