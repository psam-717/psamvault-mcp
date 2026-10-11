"""Tests for scripts/sync-skill-docs.py — the two-way merge between the installed skill and docs/skills/.

The point of the tool is that *both* copies are edit points, so these tests pin the three outcomes that
matter: a one-sided edit moves, two edits in different places merge, and two edits on the same line are
refused rather than resolved by guessing.
"""

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync-skill-docs.py"
_spec = importlib.util.spec_from_file_location("sync_skill_docs", SCRIPT)
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

PAGES = ("install", "upgrade", "usage")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=t",
                    "commit", "-qm", message], check=True, capture_output=True)


_PAGE = """---
title: {page}
description: test page
order: 1
---

# {page}

## Heading {page}
<!-- section:{page}/one -->
body from docs
<!-- /section:{page}/one -->

## See also

- repo furniture that must never leak into the installed skill
"""


def _make_case(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    (repo / "docs" / "skills").mkdir(parents=True)
    for page in PAGES:
        (repo / "docs" / "skills" / f"{page}.md").write_text(_PAGE.format(page=page), encoding="utf-8")
    installed = tmp_path / "installed" / "SKILL.md"
    installed.parent.mkdir()
    installed.write_text(
        "---\nname: psamvault-mcp\nversion: 1.0.0\n---\n\n"
        "## Heading usage\n<!-- section:usage/one -->\nbody from docs\n<!-- /section:usage/one -->\n",
        encoding="utf-8",
    )
    _git(repo, "init", "-q")
    _commit(repo, "init")
    return repo, installed


def _run(repo: Path, installed: Path, *flags: str) -> tuple[int, str]:
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = sync.main(["--repo", str(repo), "--installed", str(installed), *flags])
    return code, buf.getvalue()


# ── parsing ──────────────────────────────────────────────────────────────────────────────────────


def test_read_sections_parses_a_marked_body():
    sections = sync.read_sections("x\n<!-- section:usage/one -->\nhello\n<!-- /section:usage/one -->\ny\n",
                                  "t")
    assert list(sections) == ["usage/one"]
    assert sections["usage/one"].strip() == "hello"


def test_unclosed_marker_is_an_error():
    with pytest.raises(sync.SyncError, match="unclosed|do not match"):
        sync.read_sections("<!-- section:usage/one -->\nbody\n", "t")


def test_duplicate_section_id_is_an_error():
    text = ("<!-- section:usage/one -->\na\n<!-- /section:usage/one -->\n"
            "<!-- section:usage/one -->\nb\n<!-- /section:usage/one -->\n")
    with pytest.raises(sync.SyncError, match="twice"):
        sync.read_sections(text, "t")


def test_section_id_must_name_a_known_page():
    with pytest.raises(sync.SyncError, match="page"):
        sync.read_sections("<!-- section:nonsense/one -->\nx\n<!-- /section:nonsense/one -->\n", "t")


def test_a_section_must_live_in_the_file_its_id_names(tmp_path):
    repo = tmp_path / "repo"
    (repo / "docs" / "skills").mkdir(parents=True)
    for page in PAGES:
        (repo / "docs" / "skills" / f"{page}.md").write_text(_PAGE.format(page=page), encoding="utf-8")
    (repo / "docs" / "skills" / "usage.md").write_text(
        _PAGE.format(page="usage").replace("usage/one", "install/one"), encoding="utf-8")
    with pytest.raises(sync.SyncError, match="lives in usage.md"):
        sync.load_docs(repo)


# ── splicing ─────────────────────────────────────────────────────────────────────────────────────


def test_splice_replaces_only_the_marked_body():
    before = "head\n<!-- section:usage/one -->\nold\n<!-- /section:usage/one -->\ntail\n"
    after = sync.splice(before, {"usage/one": "new\n"})
    assert "new" in after and "old" not in after
    assert after.startswith("head\n") and after.endswith("tail\n")


def test_splice_preserves_crlf_line_endings():
    before = "head\r\n<!-- section:usage/one -->\r\nold\r\n<!-- /section:usage/one -->\r\ntail\r\n"
    after = sync.splice(before, {"usage/one": "new\n"})
    assert after.count("\r\n") == 5 and "\n\n" not in after.replace("\r\n", "")


# ── the merge itself ─────────────────────────────────────────────────────────────────────────────


def test_merge_keeps_edits_from_both_sides():
    base = "1. one\n2. two\n3. three\n"
    mine = base.replace("2. two", "2. two (installed edit)")
    theirs = base + "4. four (contributor edit)\n"
    clean, merged = sync.merge3(mine, base, theirs)
    assert clean
    assert "installed edit" in merged and "contributor edit" in merged


def test_merge_without_a_base_conflicts_when_both_sides_differ():
    clean, _ = sync.merge3("mine\n", None, "theirs\n")
    assert not clean


# ── end to end ───────────────────────────────────────────────────────────────────────────────────


def test_a_fresh_baseline_reads_as_in_step(tmp_path):
    repo, installed = _make_case(tmp_path)
    assert _run(repo, installed, "--init")[0] == 0
    code, out = _run(repo, installed, "--check")
    assert code == 0 and "in step" in out


def test_check_reports_drift_when_the_two_sides_differ(tmp_path):
    repo, installed = _make_case(tmp_path)
    _run(repo, installed, "--init")
    (repo / "docs" / "skills" / "usage.md").write_text(
        (repo / "docs" / "skills" / "usage.md").read_text(encoding="utf-8").replace(
            "body from docs", "body from docs, contributor edit"), encoding="utf-8")
    code, out = _run(repo, installed, "--check")
    assert code == 1 and "docs side has changes to pull" in out


def test_pull_brings_a_docs_edit_into_the_installed_skill(tmp_path):
    repo, installed = _make_case(tmp_path)
    _run(repo, installed, "--init")
    (repo / "docs" / "skills" / "usage.md").write_text(
        (repo / "docs" / "skills" / "usage.md").read_text(encoding="utf-8").replace(
            "body from docs", "body from docs, contributor edit"), encoding="utf-8")
    _commit(repo, "docs: contributor edit")
    code, out = _run(repo, installed, "--pull")
    text = installed.read_text(encoding="utf-8")
    assert code == 0
    assert "contributor edit" in text
    assert "repo furniture" not in text, "docs-only furniture must not leak into the installed skill"


def test_publish_brings_an_installed_edit_into_the_docs(tmp_path):
    repo, installed = _make_case(tmp_path)
    _run(repo, installed, "--init")
    installed.write_text(installed.read_text(encoding="utf-8").replace(
        "body from docs", "body from docs, installed edit"), encoding="utf-8")
    code, _ = _run(repo, installed, "--publish")
    assert code == 0
    assert "installed edit" in (repo / "docs" / "skills" / "usage.md").read_text(encoding="utf-8")


def test_a_conflicting_edit_writes_nothing(tmp_path):
    repo, installed = _make_case(tmp_path)
    _run(repo, installed, "--init")
    (repo / "docs" / "skills" / "usage.md").write_text(
        (repo / "docs" / "skills" / "usage.md").read_text(encoding="utf-8").replace(
            "body from docs", "contributor wording"), encoding="utf-8")
    _commit(repo, "docs: contributor reword")
    before = installed.read_text(encoding="utf-8")
    installed.write_text(before.replace("body from docs", "installed wording"), encoding="utf-8")
    edited = installed.read_text(encoding="utf-8")
    code, out = _run(repo, installed, "--pull")
    assert code == 1
    assert "CONFLICT" in out or "conflict" in out
    assert installed.read_text(encoding="utf-8") == edited, "a conflict must not write"


def test_pull_refuses_without_a_baseline(tmp_path):
    repo, installed = _make_case(tmp_path)
    assert _run(repo, installed, "--pull")[0] == 2


def test_init_refuses_when_the_docs_pages_are_uncommitted(tmp_path):
    repo, installed = _make_case(tmp_path)
    (repo / "docs" / "skills" / "usage.md").write_text(
        _PAGE.format(page="usage").replace("usage/one", "usage/two"), encoding="utf-8")
    assert _run(repo, installed, "--init")[0] == 2
