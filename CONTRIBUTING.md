# Contributing to psamvault-mcp

Thank you for your interest in contributing! This guide covers everything you need to get set up and submit high-quality changes.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Setting Up the Development Environment](#setting-up-the-development-environment)
3. [Project Structure](#project-structure)
4. [Making Changes](#making-changes)
5. [Running Tests](#running-tests)
6. [Commit Conventions](#commit-conventions)
7. [Submitting a Pull Request](#submitting-a-pull-request)
8. [Security Guidelines](#security-guidelines)

---

## Prerequisites

- Python ≥ 3.11
- [psamvault CLI](https://pypi.org/project/psamvault/) installed and configured
- [git](https://git-scm.com/)
- [pipx](https://pipx.pypa.io/) (recommended for tool isolation)

---

## Setting Up the Development Environment

### 1. Fork and clone the repository

```bash
git clone https://github.com/<your-username>/psamvault-mcp.git
cd psamvault-mcp
```

### 2. Create and activate a virtual environment

```bash
python -m venv mcp_venv

# Windows
mcp_venv\Scripts\activate

# macOS / Linux
source mcp_venv/bin/activate
```

### 3. Install the package in editable mode with dev dependencies

```bash
pip install -e ".[dev]"
```

> If `[dev]` extras are not yet defined, install dependencies directly:

```bash
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-mock
```

### 4. Install Playwright browsers

```bash
playwright install chromium
```

### 5. Verify your setup

```bash
pytest
```

All tests should pass with no network or keychain access required.

---

## Project Structure

```
psamvault-mcp/
├── mcp_server/           # Main package
│   ├── main.py           # Entry point — registers the MCP server
│   ├── tools.py          # MCP tool definitions (use_credential, browser_login, etc.)
│   ├── api_client.py     # HTTP client that talks to the psamvault backend
│   ├── crypto.py         # Local AES-256-GCM credential decryption
│   ├── session.py        # Keychain-based session and token management
│   ├── consent.py        # User consent dialog logic
│   ├── version_check.py  # Startup PyPI update notifier
│   └── log.py            # Structured stderr logger
├── tests/                # Pytest test suite (mirrors mcp_server/)
├── pyproject.toml        # Build config and project metadata
├── requirements.txt      # Runtime dependencies
└── README.md
```

---

## Making Changes

### Branch naming

Create a feature branch from `main`:

```bash
git checkout -b feat/my-new-feature
# or
git checkout -b fix/bug-description
```

Use the prefixes `feat/`, `fix/`, `refactor/`, `docs/`, `test/`, or `chore/` to make the intent clear.

### Adding a new MCP tool

1. Define the tool handler function in `mcp_server/tools.py`.
2. Register it with the MCP server using the `@server.tool()` decorator.
3. Add a corresponding entry to the `TOOL_REGISTRY` if one exists, or update `search_vault_tools`.
4. Write tests in `tests/test_tools.py` covering the happy path and key failure cases.
5. Update the **Available tools** table in `README.md`.

### Modifying the API client

- Keep all backend communication inside `mcp_server/api_client.py`.
- Use `httpx` for all HTTP calls — do not introduce other HTTP libraries.
- Mock `httpx` in tests using `pytest-mock`; never make real network calls in tests.

### Modifying crypto or session logic

- `crypto.py` handles local AES-256-GCM decryption — no credential value should leave this module unencrypted.
- `session.py` handles keychain storage — use the in-memory keyring mock (`conftest.py`) in all related tests.
- Any change that touches credential handling **must** be accompanied by tests.

---

## Running Tests

```bash
# Run the full test suite
pytest

# Run a specific test file
pytest tests/test_tools.py

# Run with verbose output
pytest -v

# Run a specific test by name
pytest -k "test_browser_login"
```

The test suite requires **no real network access, no OS keychain, and no running backend**. All external dependencies are mocked. If your tests need a real service to pass, reconsider the approach.

---

## Commit Conventions

This project uses [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short description>
```

| Type | When to use |
|---|---|
| `feat` | New feature or MCP tool |
| `fix` | Bug fix |
| `refactor` | Code restructuring with no behaviour change |
| `test` | Adding or updating tests |
| `docs` | Documentation only |
| `chore` | Maintenance, dependency updates, config |

**Examples:**

```
feat(tools): add revoke_session MCP tool
fix(crypto): handle missing pepper in legacy vaults
test(api_client): cover 401 refresh token flow
docs(readme): add Claude Desktop setup instructions
chore(deps): bump httpx to 0.28.0
```

- Keep the header under 72 characters.
- Use present tense and lowercase.
- One logical change per commit.

---

## Submitting a Pull Request

1. **Ensure tests pass** locally before opening a PR:
   ```bash
   pytest
   ```

2. **Push your branch** and open a PR against `main`:
   ```bash
   git push origin feat/my-new-feature
   ```

3. **Fill in the PR description** — explain _what_ changed and _why_. Link any related issues with `Closes #<issue>`.

4. **Keep PRs focused** — one feature or fix per PR. Large PRs are harder to review and slower to merge.

5. A maintainer will review your PR. Expect feedback within a few days. Address comments by pushing additional commits to the same branch — do not force-push after review has started.

---

## Security Guidelines

psamvault-mcp handles user credentials. Please take extra care:

- **Never log credential values.** Use `log.py` for all output; it should never receive a plaintext password or token.
- **Never send credentials to the agent.** The agent must only see HTTP responses or success/failure status.
- **Always require consent.** Any flow that accesses a stored credential must go through `consent.py`.
- **Do not introduce new dependencies** that make outbound network calls without a maintainer discussion first.
- If you discover a security vulnerability, **do not open a public issue**. Email the maintainer directly or use GitHub's private vulnerability reporting.
