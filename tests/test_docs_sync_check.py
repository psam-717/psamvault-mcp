"""Tests for scripts/docs-sync-check.py — the release-time doc-drift gate.

The point of the script is that documentation is updated FIRST in a release, enforced mechanically:
these tests pin both directions (clean → silent, drift → named).
"""

import importlib.util
import os as _os
import sys

ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest
from pathlib import Path

SCRIPT = Path(ROOT) / "scripts" / "docs-sync-check.py"
_spec = importlib.util.spec_from_file_location("docs_sync_check", SCRIPT)
docs_sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(docs_sync)

TOOLS = ["browser_login", "get_version", "export_key_to_env_file"]


def _make_repo(tmp_path, readme: str, count: str = "3", contract_tools=None, agents=None, skill=None):
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(
        agents if agents is not None else "\n".join(f"- `{t}`" for t in TOOLS), encoding="utf-8"
    )
    (tmp_path / "SKILL.md").write_text(
        skill if skill is not None else "\n".join(f"- `{t}`" for t in TOOLS), encoding="utf-8"
    )
    (tmp_path / "mcp_server").mkdir(exist_ok=True)
    (tmp_path / "mcp_server" / "compatibility.json").write_text(
        __import__("json").dumps({
            "schema": 1, "skill": {},
            "releases": [{"mcp": "0.5.0", "skill": "1.0.0", "breaking": False,
                          "tools": contract_tools if contract_tools is not None else TOOLS}],
        }), encoding="utf-8")
    return tmp_path


def test_the_real_repo_is_in_sync():
    """A release gate that fails on the repo it ships from is useless."""
    assert docs_sync.problems() == []


def test_clean_docs_produce_no_problems(tmp_path):
    repo = _make_repo(tmp_path, "\n".join(f"| `{t}` | does a thing |" for t in TOOLS))
    assert docs_sync.problems(repo=repo, tools=TOOLS) == []


def test_stale_tool_name_is_caught(tmp_path):
    """The exact failure a tool COUNT cannot see: a removed tool still in the docs."""
    repo = _make_repo(tmp_path, "\n".join(f"| `{t}` | x |" for t in TOOLS) + "\n| `capture_stripe_credentials` | gone |")
    found = docs_sync.problems(repo=repo, tools=TOOLS)
    assert any("capture_stripe_credentials" in f for f in found), found


def test_stale_tool_count_is_caught(tmp_path):
    repo = _make_repo(tmp_path, "The server has 14 tools.\n" + "\n".join(f"| `{t}` | x |" for t in TOOLS))
    found = docs_sync.problems(repo=repo, tools=TOOLS)
    assert any("claims 14 tools" in f for f in found), found


def test_undocumented_tool_is_caught(tmp_path):
    repo = _make_repo(tmp_path, "\n".join(f"| `{t}` | x |" for t in TOOLS[:-1]))
    found = docs_sync.problems(repo=repo, tools=TOOLS)
    assert any("does not document the tool" in f for f in found), found


def test_contract_mismatch_is_caught(tmp_path):
    repo = _make_repo(
        tmp_path,
        "\n".join(f"| `{t}` | x |" for t in TOOLS),
        contract_tools=["browser_login"],
    )
    found = docs_sync.problems(repo=repo, tools=TOOLS)
    assert any("compatibility.json" in f for f in found), found


def test_exit_code_is_one_when_drift_exists(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(docs_sync, "code_tools", lambda: TOOLS)
    monkeypatch.setattr(docs_sync, "problems", lambda *a, **k: ["README.md: something stale"])
    assert docs_sync.main() == 1
    assert "OUT OF SYNC" in capsys.readouterr().out
