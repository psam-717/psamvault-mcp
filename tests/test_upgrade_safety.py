"""Tests for mcp_server/upgrade_safety.py — the stash/smoke-test/rollback safety of `--apply`.

The core promise (inherited from psamvault-cli's upgrade path) is tested against REAL git repos, not
mocks: a dirty tree must survive an upgrade, a conflicting restore must park the work instead of
dropping it, and a failed pull must put local changes back.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest

from mcp_server import upgrade_safety as us

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "tester",
    "GIT_AUTHOR_EMAIL": "tester@example.com",
    "GIT_COMMITTER_NAME": "tester",
    "GIT_COMMITTER_EMAIL": "tester@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def _run(cwd, *args, check=True):
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=GIT_ENV)
    if check and proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}")
    return proc


def _origin_and_clone(tmp_path):
    """A bare origin plus a working clone on main with one commit."""
    origin = tmp_path / "origin.git"
    _run(tmp_path, "init", "--bare", "--initial-branch=main", str(origin))
    work = tmp_path / "work"
    _run(tmp_path, "clone", str(origin), str(work))
    (work / "a.txt").write_text("one\n", encoding="utf-8")
    _run(work, "add", "-A")
    _run(work, "commit", "-m", "init")
    _run(work, "push", "-u", "origin", "main")
    return origin, work


def _push_commit_from_elsewhere(tmp_path, origin, name: str, content: str):
    """Commit to origin through a second clone — what 'upstream moved' looks like to the first."""
    other = tmp_path / "other"
    if not other.exists():
        _run(tmp_path, "clone", str(origin), str(other))
    _run(other, "pull", "--ff-only", "origin", "main", check=False)
    (other / name).write_text(content, encoding="utf-8")
    _run(other, "add", "-A")
    _run(other, "commit", "-m", f"add {name}")
    _run(other, "push", "origin", "main")


# ── the core: stash → pull → restore ───────────────────────────────────────────
def test_dirty_and_untracked_work_survives_the_pull(tmp_path):
    origin, work = _origin_and_clone(tmp_path)
    (work / "a.txt").write_text("locally edited\n", encoding="utf-8")   # tracked, dirty
    (work / "scratch.txt").write_text("untracked\n", encoding="utf-8")  # untracked
    _push_commit_from_elsewhere(tmp_path, origin, "b.txt", "from upstream\n")

    result = us.stash_and_pull(work)

    assert result["ok"] and result["stashed"] and not result["conflict"], result
    assert (work / "b.txt").is_file(), "the upstream commit must have been pulled"
    assert (work / "a.txt").read_text(encoding="utf-8") == "locally edited\n", "edit was clobbered"
    assert (work / "scratch.txt").is_file(), "untracked work was lost"
    assert "stash" not in _run(work, "stash", "list").stdout, "the stash should have been restored"


def test_conflicting_restore_parks_the_work_instead_of_dropping_it(tmp_path):
    origin, work = _origin_and_clone(tmp_path)
    (work / "a.txt").write_text("mine\n", encoding="utf-8")
    _push_commit_from_elsewhere(tmp_path, origin, "a.txt", "theirs\n")  # same file, both sides

    result = us.stash_and_pull(work)

    assert result["ok"] and result["conflict"], result
    assert us.STASH_PREFIX in result["message"] and "git stash pop" in result["message"]
    parked = _run(work, "stash", "list").stdout
    assert us.STASH_PREFIX in parked, f"the work must still be recoverable, stash list: {parked!r}"


def test_failed_pull_restores_local_changes(tmp_path):
    origin, work = _origin_and_clone(tmp_path)
    (work / "local-commit.txt").write_text("committed locally\n", encoding="utf-8")
    _run(work, "add", "-A")
    _run(work, "commit", "-m", "local work not on origin")
    # upstream moves too, so the branch has genuinely DIVERGED (a pull --ff-only must refuse)
    _push_commit_from_elsewhere(tmp_path, origin, "b.txt", "upstream\n")
    (work / "a.txt").write_text("still mine\n", encoding="utf-8")  # and a dirty file

    result = us.stash_and_pull(work)

    assert not result["ok"] and not result["pulled"], result
    assert "restored" in result["message"] or "still stashed" in result["message"]
    assert (work / "a.txt").read_text(encoding="utf-8") == "still mine\n", "changes were not restored"
    assert "stash" not in _run(work, "stash", "list").stdout, "nothing should be left stashed here"
    assert not (work / "b.txt").exists(), "the diverged pull must not have half-applied"


def test_clean_repo_pulls_without_touching_the_stash(tmp_path):
    origin, work = _origin_and_clone(tmp_path)
    _push_commit_from_elsewhere(tmp_path, origin, "b.txt", "x\n")

    result = us.stash_and_pull(work)

    assert result["ok"] and not result["stashed"] and result["pulled"], result
    assert (work / "b.txt").is_file()


# ── git state reporting ────────────────────────────────────────────────────────
def test_repo_state_reports_dirty_ahead_and_behind(tmp_path):
    origin, work = _origin_and_clone(tmp_path)
    (work / "a.txt").write_text("dirty\n", encoding="utf-8")
    assert us.git_repo_state(work)["dirty"] is True

    _run(work, "checkout", "--", "a.txt")
    (work / "c.txt").write_text("local\n", encoding="utf-8")
    _run(work, "add", "-A")
    _run(work, "commit", "-m", "local only")
    ahead = us.git_repo_state(work)
    assert ahead["ahead"] == 1 and ahead["behind"] == 0 and ahead["branch"] == "main", ahead

    _push_commit_from_elsewhere(tmp_path, origin, "b.txt", "y\n")
    stale = us.git_repo_state(work)
    assert stale["behind"] == 0, "without a fetch, ahead/behind are measured against the stale ref"
    fresh = us.git_repo_state(work, fetch=True)
    assert fresh["behind"] == 1 and fresh["fetched"] is True, fresh
    assert "as of the last fetch" in us.render_repo_state(stale)


def test_non_repo_is_reported_not_crashed(tmp_path):
    state = us.git_repo_state(tmp_path)
    assert state["ok"] is False and "not a git repository" in state["reason"]
    assert "unknown" in us.render_repo_state(state)


def test_render_repo_state_names_the_risk(tmp_path):
    _, work = _origin_and_clone(tmp_path)
    (work / "a.txt").write_text("dirty\n", encoding="utf-8")
    assert "dirty" in us.render_repo_state(us.git_repo_state(work))


# ── editable-install detection ─────────────────────────────────────────────────
@pytest.mark.parametrize(
    "payload,expected",
    [
        # `pipx list --json` puts `venvs` at the TOP level (verified against the real command)
        ({"pipx_spec_version": "0.1", "venvs": {"psamvault-mcp": {"metadata": {"main_package": {"editable": True}}}}}, True),
        ({"venvs": {"psamvault-mcp": {"metadata": {"main_package": {"package_or_url": "file:///x"}}}}}, True),
        ({"venvs": {"psamvault-mcp": {"metadata": {"main_package": {"package_or_url": "psamvault-mcp"}}}}}, False),
        ({"venvs": {}}, False),
    ],
)
def test_is_pipx_editable_reads_pipx_json(monkeypatch, payload, expected):
    class Proc:
        returncode = 0
        stdout = __import__("json").dumps(payload)

    monkeypatch.setattr(us.subprocess, "run", lambda *a, **k: Proc())
    assert us.is_pipx_editable() is expected


def test_is_pipx_editable_is_false_when_pipx_fails(monkeypatch):
    class Proc:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(us.subprocess, "run", lambda *a, **k: Proc())
    assert us.is_pipx_editable() is False


# ── snapshot ───────────────────────────────────────────────────────────────────
def test_snapshot_skill_copies_and_prunes(tmp_path):
    skill = tmp_path / "SKILL.md"
    skill.write_text("version: 1.0.0\n", encoding="utf-8")
    backups = tmp_path / "backups"

    for index in range(7):
        skill.write_text(f"version: 1.0.{index}\n", encoding="utf-8")
        assert us.snapshot_skill(skill, backups_dir=backups, keep=5) is not None

    assert len(list(backups.glob("backup-*"))) == 5, "old snapshots must be pruned"
    assert us.snapshot_skill(tmp_path / "missing.md", backups_dir=backups) is None


# ── smoke test ─────────────────────────────────────────────────────────────────
def test_smoke_test_never_runs_with_the_repo_as_cwd(monkeypatch):
    """The recorded trap: with the repo as cwd, `import mcp_server` finds the working tree, so a
    smoke test would pass on code that was never installed."""
    captured = {}

    class Proc:
        returncode = 0
        stdout = '{"version": "9.9.9", "tools": ["a", "b"]}'
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return Proc()

    monkeypatch.setattr(us.subprocess, "run", fake_run)
    out = us.smoke_test(Path("C:/somewhere/Scripts/python.exe"))

    assert out["ok"] and out["version"] == "9.9.9" and out["tools"] == ["a", "b"]
    assert Path(captured["cwd"]).resolve() != ROOT.resolve(), "smoke test used the repo as cwd"


def test_smoke_test_reports_a_broken_install(monkeypatch):
    class Proc:
        returncode = 1
        stdout = ""
        stderr = "ImportError: no module named mcp_server"

    monkeypatch.setattr(us.subprocess, "run", lambda *a, **k: Proc())
    out = us.smoke_test(Path("C:/somewhere/Scripts/python.exe"))
    assert out["ok"] is False and "ImportError" in out["detail"]


def test_smoke_test_against_the_real_installed_server():
    """Live: the code that is actually installed must import and expose tools."""
    from mcp_server.compat import pipx_python

    interpreter = pipx_python()
    if not interpreter.is_file():
        pytest.skip(f"pipx venv not present at {interpreter}")
    out = us.smoke_test(interpreter)
    assert out["ok"], out
    assert out["tools"], "the installed server exposes no tools"


# ── rollback ───────────────────────────────────────────────────────────────────
def test_rollback_requires_a_previous_version():
    out = us.rollback(None, Path("C:/x/Scripts/python.exe"))
    assert out["ok"] is False and "no previous version" in out["message"]


def test_rollback_reinstalls_with_refresh(monkeypatch):
    """Same pitfall as the forward install: a cached index reports 'no version' right after a publish."""
    seen = {}

    class Proc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return Proc()

    monkeypatch.setattr(us.subprocess, "run", fake_run)
    out = us.rollback("0.5.1", Path("C:/x/Scripts/python.exe"))

    assert out["ok"]
    assert "--refresh" in seen["cmd"] and "psamvault-mcp==0.5.1" in seen["cmd"]
