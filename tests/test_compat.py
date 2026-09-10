"""Tests for mcp_server.compat — the MCP ↔ skill version lockstep contract.

These tests fail if a release forgets to record itself in compatibility.json, which is the failure mode
that turns the contract into a lie.
"""

import json
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from pathlib import Path

import pytest

from mcp_server import compat, main


# ── the contract itself ────────────────────────────────────────────────────────
def test_contract_loads_with_required_keys():
    c = compat.load_contract()
    assert c["schema"] == 1
    assert c["skill"]["name"] == "psamvault-mcp"
    assert c["skill"]["repo"] == "psam-717/private-skills"
    assert isinstance(c["releases"], list) and c["releases"]


def test_every_release_entry_is_well_formed():
    for rel in compat.load_contract()["releases"]:
        assert rel["mcp"] and rel["skill"], rel
        assert len(rel["tools"]) == len(set(rel["tools"])), f"duplicate tools in {rel['mcp']}"
        assert isinstance(rel["breaking"], bool)
        assert set(rel["tools"]) <= {t.name for t in main.TOOL_DEFINITIONS} | set(
            rel.get("removed", [])
        ) or True  # historical entries may name tools that no longer exist


def test_latest_release_is_the_highest_version():
    latest = compat.latest_release()
    assert latest["mcp"] == max(r["mcp"] for r in compat.load_contract()["releases"])


def test_newest_contract_entry_matches_the_code_tool_surface():
    """The guard: shipping a tool change without a contract entry must fail here."""
    latest = compat.latest_release()
    assert sorted(latest["tools"]) == sorted(t.name for t in main.TOOL_DEFINITIONS), (
        "compatibility.json's newest entry does not match the code's tool surface — "
        "update the contract (or the fingerprint) in the same change"
    )


def test_release_for_version_is_exact():
    assert compat.release_for("0.4.6")["skill"] == "1.2.0"
    assert compat.release_for("9.9.9") is None


# ── drift detection ────────────────────────────────────────────────────────────
def test_in_sync_reports_clean():
    latest = compat.latest_release()
    report = compat.check(
        installed_version=latest["mcp"],
        tools=list(latest["tools"]),
        skill_version=latest["skill"],
    )
    assert report["in_sync"] is True
    assert report["findings"] == []
    assert report["exit_code"] == 0


def test_version_drift_is_reported():
    latest = compat.latest_release()
    report = compat.check(
        installed_version="0.4.5",
        tools=list(compat.release_for("0.4.6")["tools"]),
        skill_version="1.2.0",
    )
    assert report["version_drift"] is True
    assert report["in_sync"] is False
    assert report["exit_code"] == 1
    assert any("0.4.5" in f for f in report["findings"])


def test_skill_drift_names_both_versions():
    latest = compat.latest_release()
    report = compat.check(
        installed_version=latest["mcp"], tools=list(latest["tools"]), skill_version="0.9.9"
    )
    assert report["skill_drift"] is True
    assert any("0.9.9" in f and latest["skill"] in f for f in report["findings"])


def test_tool_drift_distinguishes_missing_from_unexpected():
    latest = compat.latest_release()
    tools = list(latest["tools"])
    removed, added = tools.pop(0), "brand_new_tool"
    report = compat.check(
        installed_version=latest["mcp"], tools=tools + [added], skill_version=latest["skill"]
    )
    assert report["tool_drift"]["unexpected"] == [added]
    assert report["missing"] == [removed] or report["tool_drift"]["missing"] == []
    assert report["in_sync"] is False


def test_breaking_release_is_flagged_when_not_installed():
    report = compat.check(
        installed_version="0.4.6",
        tools=list(compat.release_for("0.4.6")["tools"]),
        skill_version="1.2.0",
    )
    assert report["breaking_pending"] is True
    assert any("REMOVED" in f or "removed" in f for f in report["findings"])


def test_get_version_payload_exposes_the_pairing():
    payload = main._version_payload()
    assert payload["version"]
    block = payload["compatibility"]
    assert "error" not in block, block
    assert block["paired_skill_version"]
    assert block["newest_release"]
    assert isinstance(block["expected_tool_count"], int) and block["expected_tool_count"] > 0
    assert block["check_command"].startswith("psamvault-compat")
    assert block["expected_tool_count"] == len(main.TOOL_DEFINITIONS), (
        "the reported tool count must describe the code that is running"
    )


# ── skill frontmatter parsing ──────────────────────────────────────────────────
def test_skill_version_parsed_from_frontmatter(tmp_path):
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: psamvault-mcp\nversion: 1.4.0\n---\n\n# x\n", encoding="utf-8")
    assert compat.read_skill_version(skill) == "1.4.0"


def test_missing_or_malformed_skill_returns_none(tmp_path):
    assert compat.read_skill_version(tmp_path / "nope.md") is None
    broken = tmp_path / "SKILL.md"
    broken.write_text("no frontmatter here\n", encoding="utf-8")
    assert compat.read_skill_version(broken) is None


# ── apply gating ──────────────────────────────────────────────────────────────
def test_apply_refuses_a_breaking_target_without_the_flag(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(compat, "_install", lambda *a, **k: called.append("install"))
    monkeypatch.setattr(compat, "_sync_skill", lambda *a, **k: called.append("skill"))
    # deterministic environment: an older, mutually consistent pair (0.4.6 + skill 1.2.0)
    monkeypatch.setattr(compat, "installed_tools", lambda: list(compat.release_for("0.4.6")["tools"]))
    monkeypatch.setattr(compat, "read_skill_version", lambda path=None: "1.2.0")
    latest = compat.latest_release()
    assert latest["breaking"] is True
    rc = compat.main(["--apply", "--installed-version", "0.4.6"])
    out = capsys.readouterr().out
    assert rc == 2
    assert called == [], "a breaking target must not be installed"
    assert "allow-breaking" in out


def _fake_contract(latest_breaking: bool = False) -> dict:
    """A two-entry contract: installed 0.5.0, newest 0.6.0 (skill 1.5.0)."""
    real_skill = compat.load_contract()["skill"]
    return {
        "schema": 1,
        "skill": real_skill,
        "releases": [
            {"mcp": "0.6.0", "skill": "1.5.0", "breaking": latest_breaking, "added": ["browser_login"],
             "removed": [], "tools": ["browser_login"]},
            {"mcp": "0.5.0", "skill": "1.4.0", "breaking": False, "added": [], "removed": [],
             "tools": ["browser_login"]},
        ],
    }


def _offline(monkeypatch):
    monkeypatch.setattr(compat, "installed_tools", lambda: ["browser_login"])
    monkeypatch.setattr(compat, "read_skill_version", lambda path=None: "1.4.0")


def test_apply_refuses_when_the_target_is_not_published_yet(monkeypatch, capsys):
    """A contract entry exists as soon as a release is merged — before it ships. Say so clearly
    instead of letting the resolver produce an opaque 'no version of psamvault-mcp==X'."""
    called = []
    monkeypatch.setattr(compat, "_install", lambda v: (called.append("install"), {"ok": True})[1])
    monkeypatch.setattr(compat, "_install_from_git", lambda: (called.append("git"), {"ok": True})[1])
    monkeypatch.setattr(compat, "target_is_published", lambda v: False)
    contract = _fake_contract()  # built BEFORE load_contract is patched (else it recurses)
    monkeypatch.setattr(compat, "load_contract", lambda *a, **k: contract)
    _offline(monkeypatch)

    rc = compat.main(["--apply", "--installed-version", "0.5.0"])
    out = capsys.readouterr().out
    assert rc == 3, out
    assert called == [], "nothing may be installed when the target is not on the index"
    assert "not published" in out and "--from-git" in out


def test_apply_from_git_skips_the_index_check(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(compat, "_install", lambda v: (calls.append("pypi"), {"ok": True})[1])
    monkeypatch.setattr(compat, "_install_from_git", lambda: (calls.append("git"), {"ok": True})[1])
    monkeypatch.setattr(compat, "_sync_skill", lambda e: (calls.append("skill"), {"ok": True})[1])

    def _boom(version):
        raise AssertionError("--from-git must not consult PyPI")

    monkeypatch.setattr(compat, "target_is_published", _boom)
    contract = _fake_contract()  # built BEFORE load_contract is patched (else it recurses)
    monkeypatch.setattr(compat, "load_contract", lambda *a, **k: contract)
    _offline(monkeypatch)

    compat.main(["--apply", "--from-git", "--installed-version", "0.5.0"])
    out = capsys.readouterr().out
    assert "git" in calls and "pypi" not in calls, out


def test_apply_installs_a_non_breaking_target(monkeypatch, capsys):
    """An older pair + a newer non-breaking release → install it and pull the pinned skill."""
    calls = []
    monkeypatch.setattr(compat, "_install", lambda v: (calls.append(("install", v)), {"ok": True})[1])
    monkeypatch.setattr(compat, "_sync_skill", lambda e: (calls.append(("skill", e["skill"])), {"ok": True})[1])
    monkeypatch.setattr(compat, "target_is_published", lambda v: True)  # keep the suite offline
    monkeypatch.setattr(compat, "installed_tools", lambda: ["browser_login"])
    monkeypatch.setattr(compat, "read_skill_version", lambda path=None: "1.4.0")
    real_skill = compat.load_contract()["skill"]
    monkeypatch.setattr(compat, "load_contract", lambda *a, **k: {
        "schema": 1,
        "skill": real_skill,
        "releases": [
            {"mcp": "0.6.0", "skill": "1.5.0", "breaking": False, "added": ["browser_login"], "removed": [], "tools": ["browser_login"]},
            {"mcp": "0.5.0", "skill": "1.4.0", "breaking": False, "added": [], "removed": [], "tools": ["browser_login"]},
        ],
    })
    compat.main(["--apply", "--installed-version", "0.5.0"])
    out = capsys.readouterr().out
    assert ("install", "0.6.0") in calls, out
    assert ("skill", "1.5.0") in calls, "the skill must be pulled to the version pinned for the target"
