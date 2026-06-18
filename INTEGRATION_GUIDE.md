# psamvault-mcp — AI Agent Integration Guide

> **Version:** v0.4.0
> **Package:** [`psamvault-mcp`](https://pypi.org/project/psamvault-mcp/) on PyPI
> **Source:** [github.com/psam-717/psamvault-mcp](https://github.com/psam-717/psamvault-mcp)
> **SDK:** [`pv-dotenv`](https://pypi.org/project/pv-dotenv/) — runtime .env placeholder resolution

---

## What is psamvault-mcp?

**psamvault-mcp** is an MCP (Model Context Protocol) server that lets AI agents use your stored credentials without ever seeing their plaintext values.

Instead of the agent reading your API keys from a `.env` file (which puts them in the agent's context window, training data, and logs), the agent calls psamvault-mcp tools that handle the credential server-side:

```
✗ Bad: Agent reads .env → API key enters context → prompt injection leaks it
✓ Good: Agent calls use_credential() → psamvault makes the HTTP request → only response returned
```

### What you can do

| Tool | What it does |
|------|-------------|
| `browser_login` | Opens a real browser, navigates to a site, fills username + password — agent never sees them |
| `use_credential` | Makes an authenticated HTTP request using a stored API key — only the HTTP response is returned |
| `run_with_credential` | Runs a shell command with a credential injected as an env var or stdin — output is redacted |
| `scan_and_protect` | Scans a project for `.env` secrets, encrypts them into psamvault, replaces with placeholders |
| `capture_stripe_credentials` | Captures credentials provisioned by `stripe projects add <provider>` |
| `list_vault_sites` | Lists stored credential sites (names only) |
| `check_credential_exists` | Checks if a credential is stored for a site |
| `get_username_for_site` | Gets stored username hint (never the password) |

---

## Installation

### 1. Install the CLI

```bash
pipx install psamvault
```

### 2. Configure and login

```bash
psamvault configure
psamvault login
```

This sets up your vault encryption key (VEK) in the OS keychain and authenticates with the psamvault backend.

### 3. Install the MCP server

```bash
pipx install psamvault-mcp
```

### 4. Install Playwright (for browser login)

```bash
playwright install chromium
```

---

## Connecting to AI Agents

### Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "psamvault": {
      "command": "psamvault-mcp"
    }
  }
}
```

Restart Claude Desktop.

### Goose

#### Option A — One-click deeplink

```
goose://extension?cmd=psamvault-mcp&timeout=300&id=psamvault&name=psamVault&description=Use%20stored%20credentials%20without%20exposing%20them%20to%20the%20agent
```

#### Option B — Manual setup in config.yaml

```yaml
extensions:
  psamvault:
    name: psamVault
    cmd: psamvault-mcp
    args: []
    enabled: true
    type: stdio
    timeout: 300
```

### Cursor / Cline

Both auto-detect `CLAUDE.md` in the project root. Clone the repo:

```bash
git clone https://github.com/psam-717/psamvault-mcp
cd psamvault-mcp
# The CLAUDE.md file tells the agent how to use the tools
```

Or add the MCP server config:

```json
// .cursor/mcp.json or .vscode/settings.json
{
  "mcpServers": {
    "psamvault": {
      "command": "psamvault-mcp"
    }
  }
}
```

### Hermes Agent

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  psamvault:
    url: "http://127.0.0.1:8433/sse"
    enabled: true
```

Then start the server:

```bash
psamvault-mcp --http --port 8433
```

### Any MCP client

Most MCP clients support stdio transport:

```json
{
  "mcpServers": {
    "psamvault": {
      "command": "psamvault-mcp"
    }
  }
}
```

For HTTP/SSE (port 8433):

```bash
psamvault-mcp --http --port 8433
# Connect client to http://127.0.0.1:8433/sse
```

---

## Tool Reference

### `browser_login`

Opens a Chromium browser, navigates to the site, finds the sign-in form, and fills in credentials.

```python
browser_login(site_name="github.com")
# Returns: {"success": true, "message": "Logged in...", "captcha_detected": false, ...}
```

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `site_name` | string | ✅ | Vault site name (e.g. `"github.com"`) |
| `login_url` | string | ❌ | Full login page URL (auto-discovered if omitted) |
| `username_selector` | string | ❌ | CSS selector for username field (auto-detected) |
| `password_selector` | string | ❌ | CSS selector for password field (auto-detected) |
| `submit_selector` | string | ❌ | CSS selector for submit button (auto-detected) |
| `timeout_ms` | integer | ❌ | Per-step detection timeout (default: 8000) |

**CAPTCHA handling:** If a CAPTCHA appears, `captcha_detected` is set to `true`. The browser pauses and the user solves the CAPTCHA manually.

**The credential is NEVER returned to the agent.** The agent only gets the success/failure response.

---

### `use_credential`

Makes an authenticated HTTP request using a stored API key. The credential is decrypted locally, injected into the request, and only the HTTP response is returned.

```python
# Bearer token (default)
use_credential(
    site_name="github.com",
    target_url="https://api.github.com/user",
    inject_as="bearer_token"
)

# API key header
use_credential(
    site_name="openai.com",
    target_url="https://api.openai.com/v1/models",
    inject_as="api_key_header",
    header_name="Authorization"
)

# Basic auth
use_credential(
    site_name="internal-api",
    target_url="https://api.internal.com/data",
    inject_as="basic_auth"
)
```

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `site_name` | string | ✅ | Vault site name or API key name |
| `target_url` | string | ✅ | The URL to make the request to |
| `method` | string | ❌ | GET, POST, PUT, PATCH, DELETE (default: GET) |
| `inject_as` | string | ❌ | `bearer_token`, `api_key_header`, or `basic_auth` (default: `bearer_token`) |
| `header_name` | string | ❌* | Required when `inject_as="api_key_header"` |
| `body` | dict | ❌ | JSON body for POST/PUT/PATCH |
| `extra_headers` | dict | ❌ | Additional HTTP headers |
| `fields` | list[str] | ❌ | Return only these response keys (reduces tokens) |

**Field filtering example:**
```python
# Full response is ~40 fields. Return only what you need:
use_credential(
    site_name="github.com",
    target_url="https://api.github.com/user",
    fields=["login", "id", "public_repos"]
)
# Returns: {"login": "psam-717", "id": 12345, "public_repos": 42}
```

**Security:** The `target_url` is validated against SSRF attacks — private IPs (10.x, 172.16.x, 192.168.x, 127.x, 169.254.x), localhost, and metadata endpoints are blocked.

---

### `run_with_credential`

Runs a shell command with a credential injected via environment variable or stdin. All output is scanned for the credential value and redacted with `[REDACTED]`.

```python
# Inject as environment variable
run_with_credential(
    site_name="pypi",
    command="twine upload dist/*",
    inject_as="env",
    env_var_name="TWINE_PASSWORD"
)
# TWINE_USERNAME is auto-set to "__token__" when env_var_name="TWINE_PASSWORD"

# Inject via stdin
run_with_credential(
    site_name="dockerhub",
    command="docker login --password-stdin",
    inject_as="stdin"
)

# With custom working directory and extra env vars
run_with_credential(
    site_name="github-api",
    command="npm publish",
    inject_as="env",
    env_var_name="NPM_TOKEN",
    extra_env={"NODE_ENV": "production"},
    workdir="/home/user/my-package",
    timeout=300
)
```

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `site_name` | string | ✅ | API key name or vault site name |
| `command` | string | ✅ | Shell command to run |
| `inject_as` | string | ❌ | `"env"` or `"stdin"` (default: `"env"`) |
| `env_var_name` | string | ❌* | Required when `inject_as="env"` |
| `extra_env` | dict | ❌ | Additional non-sensitive env vars |
| `workdir` | string | ❌ | Working directory for the subprocess |
| `timeout` | integer | ❌ | Max seconds to wait (default: 120) |

**Redaction:** The credential value AND its first 8 characters are redacted from stdout and stderr. This prevents partial leaks in log output.

---

### `scan_and_protect`

Scans a project directory for `.env` files, detects secrets using pattern matching, encrypts them into the psamvault vault, and replaces plaintext values with `psamvault:KEY_NAME` placeholders.

```python
# Scan and protect a project
scan_and_protect(project_dir="/home/user/my-project")

# With custom patterns
scan_and_protect(
    project_dir="/home/user/my-project",
    patterns=["MY_CUSTOM_KEY", "ANOTHER_SECRET"]
)
```

**What it detects:**
- API keys (`sk-...`, `pk-...`, `ghp_...`, etc.)
- Database URLs (`postgresql://user:pass@...`)
- JWT secrets, encryption keys
- Generic patterns: `TOKEN`, `SECRET`, `KEY`, `PASSWORD`, `CREDENTIALS`

After scanning, pair with **pv-dotenv** to resolve placeholders at runtime:

```python
# Before:
from dotenv import load_dotenv

# After:
from pv_dotenv import load_dotenv
load_dotenv()
# psamvault: placeholders are resolved automatically
```

---

### `capture_stripe_credentials`

After running `stripe projects add <provider>`, captures the provisioned credentials into psamvault.

```python
# Capture credentials from a Stripe project
capture_stripe_credentials(provider="neon")

# Preview without storing
capture_stripe_credentials(provider="neon", dry_run=True)

# Specify project directory
capture_stripe_credentials(provider="neon", project_dir="/home/user/my-project")
```

---

## Workflows

### Browser login
```
User: "Log me into github.com"
Agent:
  1. check_credential_exists("github.com")
  2. browser_login(site_name="github.com")
  3. Relay result to user: "Logged in successfully. The browser is open."
```

### API call
```
User: "Get my top 10 starred repos"
Agent:
  1. use_credential(
       site_name="github.com",
       target_url="https://api.github.com/users/psam-717/starred",
       fields=["name", "html_url", "description"]
     )
  2. Present repos to user
```

### Protect a project
```
User: "Secure the secrets in my project"
Agent:
  1. scan_and_protect(project_dir="/home/user/my-project")
  2. Inform user what was found
  3. Recommend: "Install pv-dotenv to resolve at runtime: pip install pv-dotenv"
```

### Deploy to PyPI
```
User: "Publish my package to PyPI"
Agent:
  1. run_with_credential(
       site_name="pypi",
       command="twine upload dist/*",
       inject_as="env",
       env_var_name="TWINE_PASSWORD"
     )
  2. Present upload result
```

---

## Example prompts

Share these with your users so they know what to ask:

- *"What credentials do I have stored?"*
- *"Log me into github.com"*
- *"Check my GitHub profile"*
- *"Get my top 10 starred repos"*
- *"Protect the secrets in my project"*
- *"Publish my package to PyPI"*
- *"Do I have a credential for z.ai?"*
- *"What's my username for Kaggle?"*

---

## Security Model

| Property | How psamvault achieves it |
|----------|--------------------------|
| **Zero-knowledge** | Server stores only AES-256-GCM ciphertext. All plaintext is decrypted client-side. |
| **No context window leak** | Credentials are decrypted in the MCP server process, injected directly into HTTP requests or subprocesses — never returned to the agent. |
| **SSRF protection** | The `use_credential` tool validates target URLs against private IP ranges, localhost, and metadata endpoints. |
| **Output redaction** | `run_with_credential` scans all stdout/stderr for the credential value and replaces it with `[REDACTED]`. |
| **Browser isolation** | `browser_login` fills credentials directly in a Chromium process — the agent never sees the values typed into forms. |
| **OS keychain** | The Vault Encryption Key (VEK) is stored in the OS keychain, never on disk as plaintext. |
| **Token auto-refresh** | Access tokens (~1hr lifetime) are automatically refreshed using refresh tokens. |

---

## Architecture

```
┌─────────────┐     MCP protocol     ┌──────────────────┐
│  AI Agent   │◄───────────────────►│  psamvault-mcp    │
│ (Claude,    │     stdio/SSE        │  (MCP Server)     │
│  Goose,     │                      │                    │
│  Hermes)    │                      │  ┌──────────────┐  │
└─────────────┘                      │  │ Playwright    │  │
                                     │  │ Chromium      │  │
                                     │  │ (browser)     │  │
                                     │  └──────────────┘  │
                                     │  ┌──────────────┐  │
                                     │  │ Subprocess    │  │
                                     │  │ Runner        │  │
                                     │  │ (cmd_runner)  │  │
                                     │  └──────────────┘  │
                                     │  ┌──────────────┐  │
                                     │  │ API Client    │──┼──► psamvault Backend
                                     │  │ (httpx)       │  │    (zero-knowledge)
                                     │  └──────────────┘  │
                                     └──────────────────┘
                                            │
                                     ┌──────▼──────┐
                                     │ OS Keychain  │
                                     │ (VEK, tokens)│
                                     └─────────────┘
```

The browser runs **in-process** with the MCP server — no subprocess daemon chain. If it crashes, the next `browser_login` call restarts it automatically.

---

## Related

| Package | What It Does | Install |
|---------|-------------|---------|
| `psamvault` | CLI — store, list, manage credentials | `pipx install psamvault` |
| `psamvault-mcp` | MCP server for AI agents | `pipx install psamvault-mcp` |
| `pv-dotenv` | Drop-in `python-dotenv` replacement | `pip install pv-dotenv` |

---

## License

MIT
