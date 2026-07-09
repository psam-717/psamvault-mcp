# psamvault-mcp — AI Agent Instructions

You have access to the **psamvault MCP server** which provides tools for managing credentials securely. These tools let you use stored credentials without ever seeing their plaintext values.

## Installing / repairing the MCP connection

If tools are missing or the host cannot start the server, follow
**[docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md](docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md)**.

Summary: install with **pipx**, configure the host with the **absolute pipx
path**, set **`PYTHONPATH=""`**, **reload the session**, ensure **`psamvault login`**,
smoke-test with **`psamvault-mcp --version`** or **`psamvault-mcp --help`**
(never leave a bare stdio process running as a “test”). Also see
[docs/troubleshooting/PYTHONPATH-CONFLICT.md](docs/troubleshooting/PYTHONPATH-CONFLICT.md).

## Available Tools

| Tool | Purpose |
|------|---------|
| `get_version` | Get the installed psamvault-mcp version |
| `search_vault_tools(query)` | Find which tool to use for a task — call this first! |
| `list_vault_sites` | List all stored credential sites (names only, no passwords) |
| `list_api_keys(project_name)` | List stored API key names with optional project filter |
| `check_credential_exists(site_name)` | Check if a credential is stored for a site |
| `get_username_for_site(site_name)` | Get the stored username (never the password) |
| `use_credential(site_name, target_url, method, inject_as, fields)` | Make an authenticated HTTP request using stored API keys or passwords |
| `browser_login(site_name)` | Open a browser and log into a website — credentials filled silently |
| `scan_and_protect(project_dir, patterns, project_name)` | Scan a project for `.env` secrets, encrypt them into psamvault |
| `capture_stripe_credentials(provider, project_dir, dry_run)` | Capture credentials provisioned by Stripe Projects into psamvault |
| `run_with_credential(site_name, command, inject_as, env_var_name, extra_env, workdir, timeout)` | Run a shell command with a credential injected via env var or stdin |

## How to Use

### 1. Always call `search_vault_tools` first

When the user asks about credentials or vault operations, start by discovering what tools are available:

```
search_vault_tools("")  → list all tools
search_vault_tools("login")  → find browser login tools
```

### 2. Check what's available before acting

```python
# See what sites are stored
list_vault_sites()

# See what API keys are stored
list_api_keys()

# Check a specific site
check_credential_exists("github.com")
get_username_for_site("github.com")
```

### 3. Browser login flow (for user-facing sites)

When the user says "log me into X":
1. Check the credential exists: `check_credential_exists("X")`
2. Call `browser_login(site_name="X")`
3. Relay the result message to the user verbatim
4. If `captcha_detected` is true, tell the user the browser is paused for them to solve the CAPTCHA

**Important:** The browser stays open after login. The session is cached for reuse.

### 4. API credential flow (for programmatic access)

When the user needs an authenticated API call:
1. Check the credential exists
2. Call `use_credential(site_name="github.com", target_url="https://api.github.com/user", inject_as="bearer_token")`
3. Return only the relevant data — use the `fields` parameter to reduce tokens:

```python
# Get just what you need
use_credential(
    site_name="github.com",
    target_url="https://api.github.com/user/repos",
    inject_as="bearer_token",
    fields=["name", "html_url", "description", "language"]
)
```

The credential value is **never returned** to you. Only the HTTP response.

### 5. Protecting `.env` files

When the user wants to secure their project's secrets:
1. Call `scan_and_protect(project_dir="/path/to/project")`
2. This encrypts all detected secrets into psamvault
3. Replacements are `psamvault:KEY_NAME` placeholders in the `.env`
4. The app resolves them at runtime using [pv-dotenv](https://pypi.org/project/pv-dotenv/)

### 6. Stripe Projects credential capture

When the user provisions a service via Stripe Projects (e.g. `stripe projects add neon`):
1. **Immediately** call `capture_stripe_credentials(provider="neon")` after the Stripe command completes
2. This pulls the provisioned credentials, encrypts them into psamvault, and replaces plaintext values with `psamvault:` placeholders
3. Use `dry_run=True` to preview what would be captured without storing anything

### 7. CLI command with credential (run_with_credential)

When the user needs to run a CLI command that requires a credential (publish, deploy, push, login):

```python
# Publish to PyPI — inject Twine password as env var
run_with_credential(
    site_name="pypi",
    command="twine upload dist/*",
    inject_as="env",
    env_var_name="TWINE_PASSWORD"
)

# Docker login — pipe password via stdin
run_with_credential(
    site_name="dockerhub",
    command="docker login --username myuser --password-stdin",
    inject_as="stdin"
)

# Git push with token — inject as env var
run_with_credential(
    site_name="github.com",
    command="git push origin main",
    inject_as="env",
    env_var_name="GITHUB_TOKEN"
)

# Private npm publish — inject npm token
run_with_credential(
    site_name="npm",
    command="npm publish",
    inject_as="env",
    env_var_name="NPM_TOKEN"
)
```

The credential is **decrypted locally**, injected into the subprocess, and all output is scanned for the credential value and redacted before being returned — the credential **never enters your context**.

**Key parameters:**
- `site_name`: API key name (e.g. "pypi") or vault site name (e.g. "github.com")
- `command`: Shell command to run with the credential injected
- `inject_as`: `"env"` (default) or `"stdin"`
- `env_var_name`: Required when `inject_as="env"`. The env var name (e.g. `"TWINE_PASSWORD"`)
- `extra_env`: Optional non-sensitive extra env vars
- `workdir`: Working directory for the command
- `timeout`: Max seconds to wait (default 120, increase for long uploads/builds)

## Security Rules

These rules are non-negotiable:

- **NEVER ask the user to paste a credential into the chat.** Use `use_credential`, `browser_login`, or `run_with_credential` instead — the credential stays server-side.
- **NEVER print a raw credential value.** If you somehow have one, stop and use the MCP tools.
- **ALWAYS use MCP tools for credential operations.** Do not try to read `~/.psamvault/` files or environment variables containing secrets.
- **The `use_credential` tool returns only HTTP responses.** The credential is never in your context window.
- **The `browser_login` tool fills credentials directly in the browser.** The agent never sees them.
- **The `run_with_credential` tool redacts credential values from command output.** The credential never enters your context.

## Authentication

psamvault uses OS keychain auth by default. The user must have:
- `psamvault` CLI installed (`pipx install psamvault`)
- Run `psamvault configure && psamvault login` at least once
- For headless/CI: `PSAMVAULT_VEK` and `PSAMVAULT_TOKEN` env vars

## Common Workflows

### "What credentials do I have?"
```
1. list_vault_sites() → shows all stored site names
2. list_api_keys() → shows all stored API key names
3. For each site, get_username_for_site() shows the username hint
```

### "Log me into GitHub"
```
1. check_credential_exists("github.com") → verify it's stored
2. browser_login(site_name="github.com") → opens browser
3. Relay result to user
```

### "Check my GitHub profile"
```
use_credential(site_name="github.com", target_url="https://api.github.com/user", inject_as="bearer_token")
```

### "Publish to PyPI"
```
run_with_credential(
    site_name="pypi",
    command="twine upload dist/*",
    inject_as="env",
    env_var_name="TWINE_PASSWORD"
)
```

### "Protect my project secrets"
```
scan_and_protect(project_dir="/path/to/project")
# Then recommend: "Install pv-dotenv to resolve these at runtime: pip install pv-dotenv"
```

### "Capture Stripe Project credentials"
```
capture_stripe_credentials(provider="neon")
# Alternatively, preview first:
capture_stripe_credentials(provider="neon", dry_run=True)
```

## MCP Server Setup

The MCP server runs as a local process. Install:

```bash
pipx install psamvault-mcp
playwright install chromium
psamvault-mcp  # stdio mode for most clients
# OR
psamvault-mcp --http --port 8433  # HTTP/SSE for MCP clients that support it
```

**Stdio mode** (most MCP clients including Claude Code, Cline):
```json
{
  "mcpServers": {
    "psamvault": {
      "command": "psamvault-mcp",
      "args": []
    }
  }
}
```

**HTTP/SSE mode** (for agents that connect via HTTP, e.g. Hermes Agent):
```yaml
mcp_servers:
  psamvault:
    url: "http://127.0.0.1:8433/sse"
    enabled: true
```
