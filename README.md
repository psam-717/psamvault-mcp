# psamvault-mcp

**v0.4.4** — MCP server for [psamvault](https://pypi.org/project/psamvault/).

Lets AI agents use your stored credentials without ever seeing their plaintext values. Also integrates with [pv-dotenv](https://pypi.org/project/pv-dotenv/) for runtime credential resolution in your `.env` files.

> **Agents installing or repairing this MCP:** read
> [docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md](docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md)
> before debugging. Common failures are PATH shadowing, corrupt pipx installs,
> missing absolute paths in client config, expired vault sessions, and
> mid-session MCP not reloading until restart.

## Features

Tools are grouped into three categories. Always start in **Entry & Orientation**.

### 🛠  Entry & Orientation
*Discover what tools are available and verify the server is running.*

| Tool | What it does |
|------|-------------|
| **`search_vault_tools`** | Discovery tool — call this first to find the right tool for your task |
| **`get_version`** | Get the installed server version |

### 🔐  Site Authentication
*End-to-end: discover, check, and log into websites.*

| Tool | What it does |
|------|-------------|
| **`list_vault_sites`** | List all stored credential sites (names and username hints only) |
| **`check_credential_exists`** | Check if a credential exists for a site |
| **`get_username_for_site`** | Get stored username (never the password) |
| **`browser_login`** | Opens Chromium, navigates to any site, fills credentials directly in the browser — agent never sees them |

### 🔑  API Key Operations
*All tools that deal with API keys — discover, use, inject, and protect.*

| Tool | What it does |
|------|-------------|
| **`list_api_keys`** | Lists all stored API key names with service hints and project grouping — never returns key values |
| **`use_credential`** | Makes authenticated HTTP requests for you (API keys, bearer tokens, basic auth) — only the HTTP response is returned |
| **`run_with_credential`** | Runs a CLI command with a credential injected via environment variable or stdin — all output redacted of the secret value |
| **`scan_and_protect`** | Scans a project directory for `.env` files, encrypts secrets into psamvault, replaces plaintext with `psamvault:KEY` placeholders |
| **`capture_stripe_credentials`** | Captures provisioned credentials from `stripe projects add <provider>` into psamvault |

> **New in v0.4.0:** `use_credential`, `run_with_credential`, `scan_and_protect`, `capture_stripe_credentials`, `list_api_keys`, single-process browser architecture (no fragile subprocess daemon), auto-restart on crash.

## How it works

### Browser login flow (`browser_login`)

When an AI agent needs to log you into a website, psamvault opens a real
Chromium browser, navigates to the site, and fills in the credentials directly
inside that browser process.

**The agent never sees the credentials.** It only sees whether the login succeeded.

```
Agent: "Log me into kaggle.com"
         ↓
psamvault opens Chromium → navigates to kaggle.com → finds the login page
         ↓
psamvault decrypts credential locally
         ↓
psamvault fills username + password fields directly in the browser
         ↓
If a CAPTCHA appears, psamvault takes a screenshot, pauses automation,
and tells you to solve the CAPTCHA and click Sign in manually
         ↓
Agent receives:
         {
           "success": true,
           "message": "Logged in to github.com successfully.",
           "steps_count": 8,
           "url": "https://github.com/dashboard",
           "captcha_detected": false
         }
         ↓
Browser stays open — you take over from there.
The browser session is saved and reused on subsequent calls to the same site.
```

### API credential flow (`use_credential`)

When an AI agent needs to make an authenticated API call on your behalf:

```
Agent: "Get my top 10 starred repos"
         ↓
use_credential("github.com", target_url="api.github.com/users/psam-717/starred")
         ↓
psamvault decrypts the API key locally, makes the HTTP request,
returns only the response — the credential is NEVER in the agent's context
```

Supports three injection modes:
- **Bearer token** — `Authorization: Bearer ***`
- **API key header** — `<custom-header>: <key>`
- **Basic auth** — `Authorization: Basic base64(user:pass)`

The `fields` parameter lets you return only the response keys you need, reducing token usage.

### CLI command flow (`run_with_credential`)

When an agent needs to run a CLI tool that requires a credential (upload to PyPI, push to a private git repo, log into Docker, publish an npm package):

```
Agent: "Upload my package to PyPI"
         ↓
run_with_credential("pypi", "twine upload dist/*",
                    inject_as="env", env_var_name="TWINE_PASSWORD")
         ↓
psamvault decrypts the credential locally, spawns the subprocess
with the credential injected as an env var (or piped via stdin)
         ↓
All stdout and stderr is scanned for the credential value
and redacted before being returned
         ↓
Agent receives only the redacted output — the credential NEVER
appears in the agent's context
```

Supports two injection modes:
- **`env`** (default) — credential set as an environment variable (e.g. `TWINE_PASSWORD`, `GITHUB_TOKEN`, `NPM_TOKEN`). When `TWINE_PASSWORD` is used, `TWINE_USERNAME=__token__` is set automatically.
- **`stdin`** — credential piped via stdin (e.g. for `docker login`).

Use cases include: `twine upload`, `git push`, `docker login`, `npm publish`, `pip install` (private repos), and any CLI tool that needs an API key or password.

### Protecting your `.env` files (`scan_and_protect`)

```
Agent: "Protect the secrets in my project"
         ↓
scan_and_protect scans the project directory for .env files
         ↓
Detects API keys, passwords, tokens (pattern matching)
         ↓
Encrypts each secret into the psamvault vault
         ↓
Replaces plaintext with "psamvault:KEY_NAME" placeholders
         ↓
Your app resolves them at runtime with pv-dotenv
```

Secrets can be stored under a project namespace by passing `project_name`:
- Keys stored as `project_name/.env/KEY_NAME` for clean per-project organisation
- When omitted, keys are stored as `env/.env/KEY_NAME` (backwards-compatible)
- Use `list_api_keys(project_name="myproject")` to view only that project's keys

After protecting, pair with [pv-dotenv](https://pypi.org/project/pv-dotenv/) — a drop-in replacement for `python-dotenv` that resolves `psamvault:` placeholders at runtime. No code changes needed beyond the import:

```python
# Before:
from dotenv import load_dotenv

# After:
from pv_dotenv import load_dotenv
```

## Prerequisites

- Python ≥ 3.11
- [psamvault](https://pypi.org/project/psamvault/) installed and logged in

```bash
pipx install psamvault
psamvault configure
psamvault login
```

- Playwright Chromium browser

```bash
playwright install chromium
```

## Installation

**Prefer pipx** (isolated venv). Avoid `pip install` into system Python — on
Windows this often leaves a broken shim on PATH that shadows the good install.

```bash
pipx install psamvault-mcp
psamvault-mcp --version   # safe smoke test (prints and exits)
psamvault-mcp --help      # same — usage text, then exit
```

### After install — resolve the real binary

If `where psamvault-mcp` / `which -a psamvault-mcp` shows **more than one**
path, configure your MCP client with the **absolute path** under your user
local bin (pipx), not the system `Python3xx\Scripts` copy:

| OS | Typical pipx path |
|----|-------------------|
| Windows | `%USERPROFILE%\.local\bin\psamvault-mcp.exe` |
| Linux / macOS | `~/.local/bin/psamvault-mcp` |

### Hardened client config (recommended)

Always pass an absolute `command` and clear `PYTHONPATH` so other tools
(e.g. Hermes) cannot contaminate imports:

```json
{
  "mcpServers": {
    "psamvault": {
      "command": "C:\\Users\\YOU\\.local\\bin\\psamvault-mcp.exe",
      "args": [],
      "env": { "PYTHONPATH": "" }
    }
  }
}
```

**Grok Build** (`~/.grok/config.toml`):

```toml
[mcp_servers.psamvault]
command = "C:\\Users\\YOU\\.local\\bin\\psamvault-mcp.exe"
args = []
enabled = true
tool_timeout_sec = 300

[mcp_servers.psamvault.env]
PYTHONPATH = ""
```

Then **restart the agent session** (or refresh MCP). Config edits do not always
reload tools mid-chat. Verify with `get_version`, then `list_vault_sites`.

Full agent playbook (corrupt pipx, PATH shadowing, session timeout, reload):
[docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md](docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md).

## Transport modes

psamvault-mcp primarily uses **stdio transport** (the MCP standard for desktop agents). HTTP/SSE transport is also available as an option.

### stdio (default — for Hermes, Goose, Claude Desktop, Cline, Grok Build)

```bash
psamvault-mcp
```

Starts the MCP server over stdin/stdout. This is the default mode and works with all major MCP desktop clients.

> **Note for agents:** a bare `psamvault-mcp` invocation **waits on stdin** for
> the MCP protocol. That is not a hang — use `--version` / `--help` for smoke
> tests, and let the host spawn the process for real use.

### HTTP/SSE (for custom clients, remote setups, or network-accessible deployments)

```bash
psamvault-mcp --http --port 8433
```

Starts an HTTP server with Server-Sent Events (SSE) transport.

| Option | Default | Description |
|--------|---------|-------------|
| `--http` | off | Enable HTTP/SSE transport |
| `--port` | `8433` | HTTP server port |
| `--host` | `127.0.0.1` | HTTP server bind address |

### Goose setup

#### Option A — One-click deeplink

Click or paste this URL into your browser while Goose Desktop is running:

```
goose://extension?cmd=psamvault-mcp&timeout=300&id=psamvault&name=psamVault&description=Use%20stored%20credentials%20without%20exposing%20them%20to%20the%20agent
```

Goose will prompt you to confirm, then the extension is added instantly.

#### Option B — Goose Desktop UI

1. Open Goose Desktop.
2. Click the **sidebar button** (top-left) → **Extensions**.
3. Click **Add custom extension**.
4. Fill in the form:

   | Field | Value |
   |---|---|
   | **Type** | `Standard IO` |
   | **ID** | `psamvault` |
   | **Name** | `psamVault` |
   | **Description** | `Use stored credentials without exposing them to the agent` |
   | **Command** | `psamvault-mcp` |
   | **Timeout** | `300` |

5. Click **Add**.

The extension appears in your Extensions list — toggle it on to activate it.

#### Option C — Config file (advanced)

Edit `~/.config/goose/config.yaml` and add the following under `extensions:`:

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

Save the file and restart Goose (or reload the session).

#### Verifying the extension works

Once added, start a Goose session and try:

```
What credentials do I have stored in my vault?
```

Goose will call `list_vault_sites` via psamvault-mcp. If you see your stored sites, everything is working.

### Hermes setup

Connect to psamvault-mcp via **stdio transport** (default). Add this block to
`~/.hermes/config.yaml` under `mcp_servers`:

```yaml
mcp_servers:
  psamvault:
    command: psamvault-mcp
    enabled: true
```

If you need HTTP/SSE transport instead (e.g. for remote access), start the server with `--http` and point Hermes at the SSE endpoint:

```yaml
mcp_servers:
  psamvault:
    url: "http://127.0.0.1:8433/sse"
    enabled: true
```

```bash
psamvault-mcp --http --port 8433
```

Restart or reload Hermes — the tools will be discovered automatically.

### Claude Desktop setup

Config file location:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\\Claude\\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "psamvault": {
      "command": "psamvault-mcp"
    }
  }
}
```

Restart Claude Desktop after saving.

### Grok Build setup

1. Install with pipx (see [Installation](#installation)).
2. Add the server to `~/.grok/config.toml` using the **absolute** pipx path
   (example above), or to `~/.mcp.json` if you already use that file.
3. Restart Grok or open `/mcps` and refresh so tools load into the session.
4. Confirm: `grok mcp doctor psamvault` → handshake OK, tools discovered.
5. Ensure the vault CLI session is active: `psamvault login`.

### Other MCP clients

Any MCP client supporting stdio transport can use psamvault-mcp.
**Prefer the absolute pipx path** over a bare command name:

```json
{
  "mcpServers": {
    "psamvault": {
      "command": "/home/YOU/.local/bin/psamvault-mcp",
      "env": { "PYTHONPATH": "" }
    }
  }
}
```

For HTTP/SSE support, point the client at `http://127.0.0.1:8433/sse`.

## Troubleshooting

| Symptom | Guide |
|---------|--------|
| MCP will not start / agent cannot connect | [docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md](docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md) |
| `pydantic` / `pydantic_core` import errors with Hermes | [docs/troubleshooting/PYTHONPATH-CONFLICT.md](docs/troubleshooting/PYTHONPATH-CONFLICT.md) |
| Index of all troubleshooting docs | [docs/troubleshooting/README.md](docs/troubleshooting/README.md) |

Quick recovery for a broken Windows pipx install:

```powershell
pipx uninstall psamvault-mcp
Remove-Item -Recurse -Force "$env:USERPROFILE\pipx\venvs\psamvault-mcp" -ErrorAction SilentlyContinue
pipx install psamvault-mcp
# Point MCP config at: $env:USERPROFILE\.local\bin\psamvault-mcp.exe
```

## Configuration

psamvault-mcp reads its backend URL from `~/.psamvault/config.env`, written
automatically by `psamvault configure`.

| Variable | Default | Description |
|---|---|---|
| `PSAMVAULT_API_URL` | `https://psam-vault-backend.onrender.com` | psamvault backend endpoint |
| `PSAMVAULT_LOG_LEVEL` | `INFO` | Log verbosity. Accepts any standard Python level: `DEBUG`, `INFO`, `WARNING`, `ERROR`. Logs go to stderr. |

To point at a self-hosted backend, set the variable in `~/.psamvault/config.env`:

```
PSAMVAULT_API_URL=https://your-backend.example.com
```

## Available tools

Tools are grouped by purpose so AI agents can find the right tool faster:

### 🛠  Entry & Orientation
*Always start here to discover what tool to use.*

| Tool | Description |
|---|---|
| `search_vault_tools` | Discovery tool — call this first to find the right tool for your task |
| `get_version` | Return the installed psamvault-mcp version |

### 🔐  Site Authentication
*End-to-end: discover, check, and log into websites.*

| Tool | Description |
|---|---|
| `list_vault_sites` | List stored site names with username hints (no passwords). Call before `browser_login` |
| `check_credential_exists` | Check if a credential exists for a site. Returns username hint |
| `get_username_for_site` | Get the username only (not password) for a site |
| `browser_login` | Open a real browser and log into a website — credentials filled silently, never shown to the agent |

### 🔑  API Key Operations
*All tools that deal with API keys — discover, use, inject, and protect.*

| Tool | Description |
|---|---|
| `list_api_keys` | List stored API key names with service hints and project grouping (never key values). Optional `project_name` filter |
| `use_credential` | Make authenticated HTTP requests using stored API keys or site passwords — only the HTTP response is returned |
| `run_with_credential` | Run a CLI command with a credential injected via env var or stdin — all output redacted of the secret |
| `scan_and_protect` | Scan a project for `.env` secrets, encrypt them into psamvault, replace with placeholders. Supports `project_name` for per-project namespacing |
| `capture_stripe_credentials` | Capture provisioned credentials from `stripe projects add <provider>` into psamvault |

## Architecture

The MCP server manages a single Playwright Chromium instance in-process.
No subprocess daemon is used — the browser lives in the same process as the
MCP server. If the browser crashes, it is automatically restarted on the
next `browser_login` call.

This eliminates the fragile 3-process chain (MCP → CLI daemon → browser)
that caused connection errors with certain MCP clients (e.g. Goose's
`ECONNREFUSED` on internal proxy ports).

## Example agent prompts

Once connected, you can ask your agent things like:

- *"What credentials do I have stored in my vault?"*
- *"What API keys do I have stored?"*
- *"Log me into kaggle.com"*
- *"Open github.com and log me in"*
- *"Check if I have a credential stored for z.ai"*
- *"Get my top 10 starred repos from GitHub"*
- *"Upload my package to PyPI"*
- *"Push to my private repo"*
- *"Protect the secrets in my project directory"*

## Related projects

| Package | What It Does |
|---------|-------------|
| [`pv-dotenv`](https://pypi.org/project/pv-dotenv/) | Drop-in replacement for `python-dotenv` — resolves `psamvault:` placeholders at runtime |
| [`psamvault-cli`](https://github.com/psam-717/psamvault-cli) | CLI + vault management — store, list, and manage credentials |

## Testing

```bash
# From the repo root
pytest
```

Tests live in `tests/` and cover crypto primitives, session management, consent
logic, the API client (with httpx mocking), and MCP tool behaviour. The test suite
requires no real network access or OS keychain — all external dependencies are mocked.

## Security

- Credentials are decrypted locally on your machine — never sent to the agent
- The agent only receives HTTP responses or redacted CLI output, never credential values
- All communication with the psamvault backend uses HTTPS
- The browser is managed in-process — no subprocess daemon or internal HTTP proxy
- CLI command output is scanned for the credential value and redacted before returning to the agent

## License

MIT — see [LICENSE](LICENSE).
