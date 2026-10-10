"""Tests for scripts/docs-touch-check.py — the docs-with-the-change gate.

The gate's whole reason to exist is that it is RELATIVE to a change: the absolute gate
(``check-docs-surface.py``) stays green while a PR adds a command that no page mentions. These tests
therefore build throwaway git repos with a base commit and a head commit, and assert the verdict for
each shape of change — added surface documented or not, removed surface still documented, code without
docs (warning, not failure), and the explicit waiver.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

SCRIPT = ROOT / "scripts" / "docs-touch-check.py"
_spec = importlib.util.spec_from_file_location("docs_touch_check", SCRIPT)
docs_touch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(docs_touch)

CLI_MAIN = '''"""Fake CLI app."""
import typer

app = typer.Typer()


@app.command("alpha")
def alpha():
    """Do alpha."""


@app.command("beta")
def beta():
    """Do beta."""
'''

def mcp_main(tools):
    """The real TOOL_DEFINITIONS shape is Tool(name="...", ...) entries — match it, not a dict."""
    entries = "\n".join(
        '    Tool(\n        name="%s",\n        description="does %s",\n    ),' % (tool, tool)
        for tool in tools
    )
    return '"""Fake MCP server."""\nTOOL_DEFINITIONS = [\n' + entries + "\n]\n"


class Repo:
    """A throwaway git repo: base commit on record, head commit to be written by the test."""

    def __init__(self, root: Path, base: str):
        self.root = root
        self.base = base

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message: str) -> str:
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-q", "-m", message], check=True, capture_output=True
        )
        return subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def run(self, capsys, *extra: str) -> tuple[int, str]:
        code = docs_touch.main([str(self.root), "--base", self.base, "--head", "HEAD", "--committed-only", *extra])
        return code, capsys.readouterr().out


def _init(root: Path) -> str:
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    for key, value in (("user.email", "t@example.com"), ("user.name", "Test")):
        subprocess.run(["git", "-C", str(root), "config", key, value], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "base"], check=True, capture_output=True)
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def cli_repo(tmp_path) -> Repo:
    # Reading a CLI surface means importing the Typer app, so these tests only apply to a repo that
    # ships one (psamvault-cli). In psamvault-mcp they skip, and the MCP-shaped tests below carry the
    # mode-independent verdicts (warning, waiver, nothing-to-do) instead.
    pytest.importorskip("typer", reason="the CLI surface gate imports the Typer app")
    root = tmp_path / "cli"
    repo = Repo(root, "")
    repo.write("main.py", CLI_MAIN)
    repo.write("command/__init__.py", "")
    repo.write(
        "docs/reference/commands.md",
        "# Command reference\n\n### psamvault alpha\n\nRuns alpha.\n",
    )
    repo.write("docs/README.md", "# Docs\n")
    repo.write("README.md", "# Readme\n")
    repo.base = _init(root)
    return repo


@pytest.fixture
def mcp_repo(tmp_path) -> Repo:
    root = tmp_path / "mcp"
    repo = Repo(root, "")
    repo.write("mcp_server/main.py", mcp_main(["alpha_tool"]))
    repo.write("docs/reference/tools.md", "# Tools\n\n| Tool | What |\n|---|---|\n| `alpha_tool` | does alpha |\n")
    repo.write("docs/README.md", "# Docs\n")
    repo.write("README.md", "# Readme\n")
    repo.base = _init(root)
    return repo


# ── added surface ──────────────────────────────────────────────────────────────
def test_a_new_command_with_no_docs_fails(cli_repo, capsys):
    cli_repo.write("command/thing.py", "X = 1\n")
    cli_repo.write(
        "main.py",
        CLI_MAIN.replace("@app.command(\"alpha\")", "@app.command(\"gamma\")\ndef gamma():\n    \"\"\"G.\"\"\"\n\n\n@app.command(\"alpha\")"),
    )
    cli_repo.commit("feat: add gamma")
    code, out = cli_repo.run(capsys)
    assert code == 1, out
    assert "psamvault gamma" in out
    assert "docs/reference/commands.md" in out


def test_a_new_command_documented_in_the_same_change_passes(cli_repo, capsys):
    cli_repo.write("command/thing.py", "X = 1\n")
    cli_repo.write(
        "main.py",
        CLI_MAIN.replace("@app.command(\"alpha\")", "@app.command(\"gamma\")\ndef gamma():\n    \"\"\"G.\"\"\"\n\n\n@app.command(\"alpha\")"),
    )
    cli_repo.write(
        "docs/reference/commands.md",
        "# Command reference\n\n### psamvault alpha\n\nRuns alpha.\n\n### psamvault gamma\n\nRuns gamma.\n",
    )
    cli_repo.commit("feat: add gamma + docs")
    code, out = cli_repo.run(capsys)
    assert code == 0, out
    assert "the docs move with the code" in out


def test_a_new_mcp_tool_with_no_docs_fails(mcp_repo, capsys):
    mcp_repo.write("mcp_server/main.py", mcp_main(["alpha_tool", "beta_tool"]))
    mcp_repo.commit("feat: add beta_tool")
    code, out = mcp_repo.run(capsys)
    assert code == 1, out
    assert "tool `beta_tool`" in out


def test_a_behaviour_change_without_docs_warns_in_mcp_mode(mcp_repo, capsys):
    mcp_repo.write("mcp_server/client.py", "def send():\n    return 1\n")
    mcp_repo.commit("fix: internal behaviour")
    code, out = mcp_repo.run(capsys)
    assert code == 0, out
    assert "warning" in out


def test_the_waiver_records_the_decision_in_mcp_mode(mcp_repo, capsys):
    mcp_repo.write("mcp_server/client.py", "def send():\n    return 1\n")
    mcp_repo.commit("refactor: internal only")
    code, out = mcp_repo.run(capsys, "--allow-no-docs", "pure refactor")
    assert code == 0, out
    assert "waived by decision — pure refactor" in out


def test_a_new_environment_variable_with_no_docs_fails(cli_repo, capsys):
    cli_repo.write("api_client.py", 'import os\n\nURL = os.environ.get("PSAMVAULT_API_URL")\nTIMEOUT = os.environ.get("PSAMVAULT_PROBE_TIMEOUT")\n')
    cli_repo.commit("feat: configurable probe timeout")
    code, out = cli_repo.run(capsys)
    assert code == 1, out
    assert "PSAMVAULT_PROBE_TIMEOUT" in out


# ── removed surface ────────────────────────────────────────────────────────────
def test_a_removed_command_still_named_in_the_docs_fails(cli_repo, capsys):
    cli_repo.write("main.py", CLI_MAIN.replace('@app.command("beta")\ndef beta():\n    """Do beta."""\n', ""))
    cli_repo.write(
        "docs/reference/commands.md",
        "# Command reference\n\n### psamvault alpha\n\nRuns alpha.\n\n### psamvault beta\n\nRuns beta.\n",
    )
    cli_repo.commit("feat: drop beta")
    code, out = cli_repo.run(capsys)
    assert code == 1, out
    assert "still name it" in out


# ── the warning, and the waiver ────────────────────────────────────────────────
def test_behaviour_change_without_docs_warns_but_passes(cli_repo, capsys):
    cli_repo.write("api_client.py", "def send():\n    return 1\n")
    cli_repo.commit("fix: behaviour with no new surface")
    code, out = cli_repo.run(capsys)
    assert code == 0, out
    assert "warning" in out
    assert "::warning" in out


def test_the_waiver_records_the_decision_and_passes(cli_repo, capsys):
    cli_repo.write("api_client.py", "def send():\n    return 1\n")
    cli_repo.commit("refactor: internal only")
    code, out = cli_repo.run(capsys, "--allow-no-docs", "pure refactor")
    assert code == 0, out
    assert "waived by decision — pure refactor" in out


def test_the_waiver_still_reports_an_undocumented_new_command(cli_repo, capsys):
    """The label is a decision, not a blindfold: the finding stays in the log."""
    cli_repo.write(
        "main.py",
        CLI_MAIN.replace("@app.command(\"alpha\")", "@app.command(\"gamma\")\ndef gamma():\n    \"\"\"G.\"\"\"\n\n\n@app.command(\"alpha\")"),
    )
    cli_repo.commit("feat: add gamma")
    code, out = cli_repo.run(capsys, "--allow-no-docs", "deliberate")
    assert code == 0, out
    assert "still worth knowing" in out
    assert "psamvault gamma" in out


# ── nothing to do ──────────────────────────────────────────────────────────────
def test_a_tests_only_change_is_not_even_a_warning(cli_repo, capsys):
    cli_repo.write("tests/test_thing.py", "def test_x():\n    assert True\n")
    cli_repo.commit("test: add a test")
    code, out = cli_repo.run(capsys)
    assert code == 0, out
    assert "no user-facing code changed" in out


def test_an_unresolvable_base_exits_2_instead_of_passing(cli_repo, capsys):
    cli_repo.write("api_client.py", "X = 1\n")
    cli_repo.commit("fix: something")
    code = docs_touch.main([str(cli_repo.root), "--base", "origin/nope", "--head", "HEAD", "--committed-only"])
    assert code == 2
    assert "cannot compare" in capsys.readouterr().err


def test_the_real_repo_is_clean_against_its_own_base(cli_repo, capsys):
    """A gate that fails on the change that introduces it is unusable."""
    cli_repo.write("scripts/docs-touch-check.py", "# the script itself is not user-facing code\n")
    cli_repo.commit("ci: add the gate")
    code, out = cli_repo.run(capsys)
    assert code == 0, out
