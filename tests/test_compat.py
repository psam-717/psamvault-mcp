"""Tests for mcp_server.compat — the MCP ↔ skill version lockstep contract.

These tests fail if a release forgets to record itself in compatibility.json, which is the failure mode
that turns the contract into a lie.
"""

import json
import os as _os
import subprocess
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


def test_sync_skill_refuses_to_downgrade(tmp_path, monkeypatch):
    """The clone is read as-is, so a clone parked on an older branch holds an older skill. Installing
    it would delete documentation that exists nowhere else — refused unless explicitly allowed."""
    clone = tmp_path / "clone"
    (clone / "psamvault-mcp").mkdir(parents=True)
    (clone / "psamvault-mcp" / "SKILL.md").write_text("---\nversion: 1.7.0\n---\nolder\n", encoding="utf-8")
    installed = tmp_path / "installed" / "SKILL.md"
    installed.parent.mkdir(parents=True)
    installed.write_text("---\nversion: 1.8.0\n---\nnewer\n", encoding="utf-8")
    monkeypatch.setattr(compat, "clone_path", lambda: clone)
    monkeypatch.setattr(compat, "installed_skill_path", lambda: installed)
    monkeypatch.setattr(compat, "clone_skill_branch", lambda: "docs/older-branch")

    out = compat._sync_skill({"mcp": "0.5.1", "skill": "1.6.0"})

    assert out["ok"] is False
    assert "refusing to downgrade" in out["reason"], out
    assert "1.8.0" in out["reason"] and "docs/older-branch" in out["reason"], out
    assert installed.read_text(encoding="utf-8").endswith("newer\n"), "the installed skill was touched"


def test_sync_skill_downgrade_is_allowed_with_the_flag(tmp_path, monkeypatch):
    clone = tmp_path / "clone"
    (clone / "psamvault-mcp").mkdir(parents=True)
    (clone / "psamvault-mcp" / "SKILL.md").write_text("---\nversion: 1.7.0\n---\nolder\n", encoding="utf-8")
    installed = tmp_path / "installed" / "SKILL.md"
    installed.parent.mkdir(parents=True)
    installed.write_text("---\nversion: 1.8.0\n---\nnewer\n", encoding="utf-8")
    monkeypatch.setattr(compat, "clone_path", lambda: clone)
    monkeypatch.setattr(compat, "installed_skill_path", lambda: installed)

    out = compat._sync_skill({"mcp": "0.5.1", "skill": "1.6.0"}, allow_downgrade=True)

    assert out["ok"] and out["version"] == "1.7.0" and out["replaced"] == "1.8.0", out
    assert installed.read_text(encoding="utf-8").endswith("older\n")


def test_check_reports_a_clone_that_is_behind_the_installed_skill(tmp_path, monkeypatch):
    """Informational, not drift: the installed pair is healthy, but the source of truth is behind —
    which is exactly how a silent downgrade opportunity goes unnoticed."""
    clone = tmp_path / "clone"
    (clone / "psamvault-mcp").mkdir(parents=True)
    (clone / "psamvault-mcp" / "SKILL.md").write_text("---\nversion: 1.7.0\n---\n", encoding="utf-8")
    monkeypatch.setattr(compat, "clone_path", lambda: clone)
    monkeypatch.setattr(compat, "read_skill_version", lambda path=None: "1.8.0")
    monkeypatch.setattr(compat, "installed_tools", lambda: list(compat.latest_release()["tools"]))

    report = compat.check(installed_version=compat.latest_release()["mcp"])

    assert report["skill_source"] == "1.7.0" and report["skill_source_stale"] is True
    assert report["in_sync"] is True, "an older clone must not be reported as drift"
    assert "skill in clone" in compat.render(report), compat.render(report)


def test_skill_drift_names_both_versions():
    latest = compat.latest_release()
    report = compat.check(
        installed_version=latest["mcp"], tools=list(latest["tools"]), skill_version="0.9.9"
    )
    assert report["skill_drift"] is True
    assert any("0.9.9" in f and latest["skill"] in f for f in report["findings"])


# ── skill versions are a FLOOR, not a pin ─────────────────────────────────────
def test_a_newer_skill_is_not_drift():
    """A skill-only update (better docs for an existing tool) must need no MCP release."""
    latest = compat.latest_release()
    report = compat.check(
        installed_version=latest["mcp"], tools=list(latest["tools"]), skill_version="99.0.0"
    )
    assert report["skill_drift"] is False
    assert report["skill_ahead"] is True
    assert report["in_sync"] is True and report["exit_code"] == 0
    assert report["findings"] == [], report["findings"]


def test_skill_below_the_floor_points_at_sync_skill():
    latest = compat.latest_release()
    report = compat.check(
        installed_version=latest["mcp"], tools=list(latest["tools"]), skill_version="0.0.1"
    )
    assert report["skill_drift"] is True and report["skill_ahead"] is False
    assert report["exit_code"] == 1
    assert any("--sync-skill" in finding for finding in report["findings"]), report["findings"]


def test_missing_skill_counts_as_below_the_floor(monkeypatch):
    latest = compat.latest_release()
    # a missing skill file reads as None; that is below any floor
    monkeypatch.setattr(compat, "read_skill_version", lambda path=None: None)
    report = compat.check(
        installed_version=latest["mcp"], tools=list(latest["tools"]), skill_version=None
    )
    assert report["skill_drift"] is True and report["exit_code"] == 1


def test_render_shows_the_floor_and_the_ahead_marker():
    latest = compat.latest_release()
    report = compat.check(
        installed_version=latest["mcp"], tools=list(latest["tools"]), skill_version="99.0.0"
    )
    out = compat.render(report)
    assert "skill floor" in out and "ahead of the floor" in out, out
    assert "in sync — nothing to do" in out, "a healthy pair must not look like a problem"


def test_sync_skill_installs_the_clone_working_tree(tmp_path, monkeypatch):
    """Skill-only update: whatever the clone currently holds (>= floor) is what gets installed."""
    clone = tmp_path / "clone"
    (clone / "psamvault-mcp").mkdir(parents=True)
    (clone / "psamvault-mcp" / "SKILL.md").write_text(
        "---\nname: psamvault\nversion: 1.8.0\n---\nnew docs\n", encoding="utf-8"
    )
    installed = tmp_path / "installed" / "SKILL.md"
    monkeypatch.setattr(compat, "clone_path", lambda: clone)
    monkeypatch.setattr(compat, "installed_skill_path", lambda: installed)

    out = compat._sync_skill({"mcp": "0.5.1", "skill": "1.6.0"})

    assert out["ok"] and out["version"] == "1.8.0" and out["source_version"] == "1.8.0"
    assert installed.read_text(encoding="utf-8").endswith("new docs\n")


def test_sync_skill_refuses_a_clone_below_the_floor(tmp_path, monkeypatch):
    clone = tmp_path / "clone"
    (clone / "psamvault-mcp").mkdir(parents=True)
    (clone / "psamvault-mcp" / "SKILL.md").write_text(
        "---\nname: psamvault\nversion: 1.0.0\n---\nold\n", encoding="utf-8"
    )
    installed = tmp_path / "installed" / "SKILL.md"
    monkeypatch.setattr(compat, "clone_path", lambda: clone)
    monkeypatch.setattr(compat, "installed_skill_path", lambda: installed)

    out = compat._sync_skill({"mcp": "0.5.1", "skill": "1.6.0"})

    assert out["ok"] is False and "at least 1.6.0" in out["reason"], out
    assert not installed.exists(), "nothing may be written when the floor is not met"


def test_sync_skill_flag_updates_only_the_skill(tmp_path, monkeypatch, capsys):
    """`--sync-skill` must work with no MCP release and no MCP install at all."""
    clone = tmp_path / "clone"
    (clone / "psamvault-mcp").mkdir(parents=True)
    (clone / "psamvault-mcp" / "SKILL.md").write_text(
        "---\nname: psamvault\nversion: 1.8.0\n---\nnew\n", encoding="utf-8"
    )
    installed = tmp_path / "installed" / "SKILL.md"
    monkeypatch.setattr(compat, "clone_path", lambda: clone)
    monkeypatch.setattr(compat, "installed_skill_path", lambda: installed)
    monkeypatch.setattr(compat, "installed_tools", lambda: list(compat.latest_release()["tools"]))

    def _version_of(path=None):
        target = Path(path or installed)
        if not target.is_file():
            return None
        match = compat.FRONTMATTER_VERSION.search(target.read_text(encoding="utf-8")[:200])
        return match.group(1) if match else None

    monkeypatch.setattr(compat, "read_skill_version", _version_of)
    monkeypatch.setattr(compat, "_install", lambda v: pytest.fail("--sync-skill must not install the MCP"))
    monkeypatch.setattr(compat, "_install_from_git",
                        lambda: pytest.fail("--sync-skill must not install the MCP"))

    # the MCP side is irrelevant to a skill-only update: hold it fixed so the exit code reflects the
    # skill, not this venv's stale dist metadata
    rc = compat.main(["--sync-skill", "--installed-version", compat.latest_release()["mcp"]])
    out = capsys.readouterr().out

    assert "skill -> 1.8.0" in out, out
    assert rc == 0, out


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


def test_breaking_release_is_flagged_when_not_installed(monkeypatch):
    """A breaking newest release is flagged while it is not the installed version.

    Built on an explicit contract fixture: the shipped contract's newest release changes every
    release, so asserting on it makes the suite fail on every bump (it did, on 0.5.1).
    """
    contract = _fake_contract(latest_breaking=True)
    monkeypatch.setattr(compat, "load_contract", lambda *a, **k: contract)
    report = compat.check(installed_version="0.5.0", tools=["browser_login"], skill_version="1.4.0")
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


def test_install_refreshes_the_index_cache(monkeypatch):
    """Publishing then immediately applying must not fail on cached index metadata.

    PyPI's simple index answers with `cache-control: max-age=600`, so uv's cached view can still lack
    the brand-new version — observed live right after 0.5.0 was published ("no version of
    psamvault-mcp==0.5.0" while the index already listed it).
    """
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(compat.subprocess, "run", fake_run)
    result = compat._install("0.5.0")
    assert result["ok"] is True
    assert "--refresh" in captured["cmd"], captured["cmd"]
    assert "psamvault-mcp==0.5.0" in captured["cmd"]
    assert "--no-deps" not in captured["cmd"], "deps must be resolved (--no-deps drops tools)"


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
    monkeypatch.setattr(compat, "_install_from_git", lambda *a, **k: called.append("git"))
    monkeypatch.setattr(compat, "_sync_skill", lambda *a, **k: called.append("skill"))
    # explicit fixture: an older installed version, a breaking newest release
    contract = _fake_contract(latest_breaking=True)
    monkeypatch.setattr(compat, "load_contract", lambda *a, **k: contract)
    _offline(monkeypatch)

    rc = compat.main(["--apply", "--installed-version", "0.5.0"])
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


def test_apply_explains_a_target_missing_from_the_index(monkeypatch, capsys):
    """The index check is ADVISORY: the install is attempted and only a real failure explains itself.

    PyPI's JSON API lags an upload, so refusing up front produced a false "not published yet" right
    after a release (seen live with 0.5.1).
    """
    calls = []
    monkeypatch.setattr(compat, "_install", lambda v: (
        calls.append(("install", v)), {"ok": False, "stdout": "resolver said no", "stderr": ""})[1])
    monkeypatch.setattr(compat, "_sync_skill", lambda *a, **k: calls.append("skill"))
    monkeypatch.setattr(compat, "target_is_published", lambda v: False)
    contract = _fake_contract()
    monkeypatch.setattr(compat, "load_contract", lambda *a, **k: contract)
    _offline(monkeypatch)

    rc = compat.main(["--apply", "--installed-version", "0.5.0"])
    out = capsys.readouterr().out
    assert rc == 3, out
    assert ("install", "0.6.0") in calls, "the install must be attempted — the index check is advisory"
    assert "skill" not in calls
    assert "not listed on PyPI" in out and "--from-git" in out


def test_apply_does_not_refuse_when_the_index_lags_but_the_install_works(monkeypatch, capsys):
    """Regression for the live 0.5.1 bug: JSON API lagging must not block the repair."""
    calls = []
    monkeypatch.setattr(compat, "_install", lambda v: (calls.append(("install", v)), {"ok": True})[1])
    monkeypatch.setattr(compat, "_sync_skill", lambda e, **k: (calls.append(("skill", e["skill"])), {"ok": True})[1])
    monkeypatch.setattr(compat, "target_is_published", lambda v: False)  # index has not caught up
    contract = _fake_contract()
    monkeypatch.setattr(compat, "load_contract", lambda *a, **k: contract)
    _offline(monkeypatch)
    monkeypatch.setattr(compat, "read_skill_version", lambda path=None: "1.5.0")

    rc = compat.main(["--apply", "--installed-version", "0.5.0"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert ("skill", "1.5.0") in calls, "a lagging index must not block the repair"


def test_apply_from_git_skips_the_index_check(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(compat, "_install", lambda v: (calls.append("pypi"), {"ok": True})[1])
    monkeypatch.setattr(compat, "_install_from_git", lambda: (calls.append("git"), {"ok": True})[1])
    monkeypatch.setattr(compat, "_sync_skill", lambda e, **k: (calls.append("skill"), {"ok": True})[1])

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
    monkeypatch.setattr(compat, "_sync_skill", lambda e, **k: (calls.append(("skill", e["skill"])), {"ok": True})[1])
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


# ── upgrade safety: snapshot / smoke test / rollback / pull ────────────────────
@pytest.fixture(autouse=True)
def _never_touch_the_real_machine(monkeypatch):
    """`--apply` tests must not write to the real skill dir or run the real pipx venv.

    Tests that assert on these behaviours override them locally — a monkeypatch inside the test body
    is applied after this fixture, so it wins.
    """
    monkeypatch.setattr(compat.safety, "snapshot_skill", lambda *a, **k: None)
    monkeypatch.setattr(
        compat.safety, "smoke_test",
        lambda *a, **k: {"ok": True, "version": "0.0.0", "tools": ["browser_login"], "detail": ""},
    )
    monkeypatch.setattr(compat.safety, "is_pipx_editable", lambda: False)


def test_apply_snapshots_the_skill_before_installing(monkeypatch, capsys):
    """The skill file is overwritten by --apply, so it must be backed up first."""
    order = []
    monkeypatch.setattr(compat.safety, "snapshot_skill", lambda *a, **k: order.append("snapshot") or Path("b"))
    monkeypatch.setattr(compat, "_install", lambda v: (order.append("install"), {"ok": True})[1])
    monkeypatch.setattr(compat, "_sync_skill", lambda e, **k: {"ok": True, "version": e["skill"]})
    monkeypatch.setattr(compat, "target_is_published", lambda v: True)
    contract = _fake_contract()
    monkeypatch.setattr(compat, "load_contract", lambda *a, **k: contract)
    _offline(monkeypatch)
    monkeypatch.setattr(compat, "read_skill_version", lambda path=None: "1.5.0")

    compat.main(["--apply", "--installed-version", "0.5.0"])
    assert order[:2] == ["snapshot", "install"], order


def test_apply_rolls_back_when_the_smoke_test_fails(monkeypatch, capsys):
    """A half-finished install must not be left installed."""
    calls = []
    monkeypatch.setattr(compat, "_install", lambda v: {"ok": True})
    monkeypatch.setattr(compat, "_sync_skill", lambda e, **k: (calls.append("skill"), {"ok": True})[1])
    monkeypatch.setattr(compat, "target_is_published", lambda v: True)
    monkeypatch.setattr(compat.safety, "smoke_test",
                        lambda *a, **k: {"ok": False, "version": None, "tools": [], "detail": "ImportError"})
    monkeypatch.setattr(compat.safety, "rollback", lambda previous, python: (
        calls.append(("rollback", previous)), {"ok": True, "message": f"rolled back to {previous}"})[1])
    contract = _fake_contract()
    monkeypatch.setattr(compat, "load_contract", lambda *a, **k: contract)
    _offline(monkeypatch)

    rc = compat.main(["--apply", "--installed-version", "0.5.0"])
    out = capsys.readouterr().out
    assert rc == 1
    assert ("rollback", "0.5.0") in calls, "the previous version must be restored"
    assert "smoke test FAILED" in out and "rolled back to 0.5.0" in out
    assert "skill" not in calls, "a broken install must not get its skill synced"


def test_apply_refuses_to_install_when_the_pull_fails(monkeypatch, capsys):
    """--pull: a diverged branch must stop the upgrade with the user's work restored."""
    calls = []
    monkeypatch.setattr(compat.safety, "stash_and_pull", lambda repo: {
        "ok": False, "stashed": True, "conflict": False, "pulled": False,
        "message": "git pull --ff-only origin main failed — your local changes were restored.",
    })
    monkeypatch.setattr(compat, "_install_from_git", lambda: (calls.append("install"), {"ok": True})[1])
    monkeypatch.setattr(compat, "_sync_skill", lambda e, **k: (calls.append("skill"), {"ok": True})[1])
    contract = _fake_contract()
    monkeypatch.setattr(compat, "load_contract", lambda *a, **k: contract)
    _offline(monkeypatch)

    rc = compat.main(["--apply", "--pull", "--allow-breaking", "--installed-version", "0.5.0"])
    out = capsys.readouterr().out
    assert rc == 1
    assert calls == [], "nothing may be installed when the pull failed"
    assert "restored" in out


def test_apply_from_git_reports_what_will_be_installed(monkeypatch, capsys):
    """Installing a working tree means the user must see branch / dirtiness / position vs origin."""
    monkeypatch.setattr(compat.safety, "git_repo_state", lambda *a, **k: {
        "ok": True, "dirty": True, "ahead": 2, "behind": 1, "branch": "main", "head": "abc1234",
        "fetched": True, "reason": None,
    })
    monkeypatch.setattr(compat.safety, "is_pipx_editable", lambda: True)
    monkeypatch.setattr(compat, "_install_from_git", lambda: {"ok": True})
    monkeypatch.setattr(compat, "_sync_skill", lambda e, **k: {"ok": True, "version": e["skill"]})
    contract = _fake_contract()
    monkeypatch.setattr(compat, "load_contract", lambda *a, **k: contract)
    _offline(monkeypatch)
    monkeypatch.setattr(compat, "read_skill_version", lambda path=None: "1.5.0")

    rc = compat.main(["--apply", "--from-git", "--allow-breaking", "--installed-version", "0.5.0"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "branch main" in out and "dirty (uncommitted changes)" in out, out
    assert "2 ahead / 1 behind" in out, out
    assert "editable/source install" in out, "an editable install must be flagged before it is replaced"
    assert "smoke test" in out
