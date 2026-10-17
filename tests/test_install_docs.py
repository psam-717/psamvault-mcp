"""The public install pages must stay followable on a machine with no private checkout.

Issue #60: `playwright` is not on PATH after `pipx install`, and current pipx does not use
`~/.local/pipx/venvs`. Issue #59: the README must not send a public user to the private skill repo.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Pages a person or an agent follows to install. CONTRIBUTING.md activates a local venv first,
# so its `playwright install chromium` is a different command and is not in this list.
PUBLIC_INSTALL_DOCS = (
    "README.md",
    "CLAUDE.md",
    "AGENTS.md",
    "PLAYWRIGHT_INTEGRATION.md",
    "docs/installation.md",
    "docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md",
    "docs/guides/browser-login.md",
    "docs/reference/configuration.md",
    "docs/skills/install.md",
    "docs/skills/upgrade.md",
    "docs/skills/README.md",
    "mcp_server/prompts/general-rules.md",
    "mcp_server/agent_guide.py",
)

PLAYWRIGHT_PAGES = (
    "README.md",
    "docs/installation.md",
    "docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md",
    "docs/guides/browser-login.md",
    "docs/skills/install.md",
    "CLAUDE.md",
)


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_public_docs_do_not_link_the_private_skill_repo():
    for rel in PUBLIC_INSTALL_DOCS:
        text = _text(rel)
        assert "github.com/psam-717/private-skills" not in text, rel
        assert "clone private-skills" not in text, rel


def test_public_docs_do_not_hardcode_the_legacy_pipx_venv():
    """`~/.local/pipx/venvs` is not where current pipx keeps venvs. A sentence that
    tells the reader not to use that path is fine; a command that uses it is not."""
    banned = ("~/.local/pipx/venvs", "~/pipx/venvs")
    for rel in PUBLIC_INSTALL_DOCS:
        for line in _text(rel).splitlines():
            for bad in banned:
                if bad not in line:
                    continue
                lowered = line.lower()
                assert any(phrase in lowered for phrase in ("do not", "don't", "never ")), (rel, line)


def test_install_pages_ask_pipx_and_use_the_venv_interpreter():
    for rel in ("docs/installation.md", "docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md", "README.md"):
        text = _text(rel)
        assert "pipx environment --value PIPX_LOCAL_VENVS" in text, rel
        assert "-m playwright install chromium" in text, rel


def test_browser_pages_do_not_tell_the_user_to_run_a_bare_playwright():
    for rel in PLAYWRIGHT_PAGES:
        text = _text(rel)
        # The bare command is what pipx does not provide. The venv form contains the same words
        # after `python -m`, which is the command that works.
        assert "python -m playwright install chromium" in text or "-m playwright install chromium" in text, rel
        bare = [
            line
            for line in text.splitlines()
            if line.strip() in {"playwright install chromium", "`playwright install chromium`."}
            or line.strip().startswith("playwright install chromium")
        ]
        assert bare == [], (rel, bare)


def test_agent_instructions_do_not_invent_a_headless_key_pair():
    """CLAUDE.md used to tell agents that CI authenticates with VEK/TOKEN. The server does not read them."""
    claude = _text("CLAUDE.md")
    assert "does not" in claude and "PSAMVAULT_VEK" in claude
    assert "For headless/CI: `PSAMVAULT_VEK`" not in claude
