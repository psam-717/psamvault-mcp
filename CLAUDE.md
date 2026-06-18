# psamvault-mcp — AI Agent Instructions

You have access to the **psamvault MCP server** which provides tools for managing credentials securely. These tools let you use stored credentials without ever seeing their plaintext values.

## Available Tools

| Tool | Purpose |
|------|---------|
| `get_version` | Get the installed psamvault-mcp version |
| `search_vault_tools(vault_tools)` | Find which tool to use for a task — call this first! |
| `list_vault_sites` | List all stored credential sites (names only, no passwords) |
| `check_credential_exists(site_name)` | Check if a credential is stored for a site |
| `get_username_for_site(site_name)` | Get the stored username (never the password) |
| `use_credential(site_name, target_url, method, inject_as)` | Make an authenticated HTTP request using stored API keys |
| `browser_login(site_name)` | Open a browser and log into a website — credentials filled silently |
| `scan_and_protect(project_dir)` | Scan a project for `.env` secrets, encrypt them into psamvault |

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

## Security Rules

These rules are non-negotiable:

- **NEVER ask the user to paste a credential into the chat.** Use `use_credential` or `browser_login` instead — the credential stays server-side.
- **NEVER print a raw credential value.** If you somehow have one, stop and use the MCP tools.
- **ALWAYS use MCP tools for credential operations.** Do not try to read `~/.psamvault/` files or environment variables containing secrets.
- **The `use_credential` tool returns only HTTP responses.** The credential is never in your context window.
- **The `browser_login` tool fills credentials directly in the browser.** The agent never sees them.

## Authentication

psamvault uses OS keychain auth by default. The user must have:
- `psamvault` CLI installed (`pipx install psamvault`)
- Run `psamvault configure && psamvault login` at least once
- For headless/CI: `PSAMVAULT_VEK` and `PSAMVAULT_TOKEN` env vars

## Common Workflows

### "What credentials do I have?"
```
1. list_vault_sites() → shows all stored site names
2. For each site, get_username_for_site() shows the username hint
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

### "Protect my project secrets"
```
scan_and_protect(project_dir="/path/to/project")
# Then recommend: "Install pv-dotenv to resolve these at runtime: pip install pv-dotenv"
```

## MCP Server Setup

The MCP server runs as a local process. Install:

```bash
pipx install psamvault-mcp
playwright install chromium
psamvault-mcp  # stdio mode for most clients
# OR
psamvault-mcp --http --port 8433  # HTTP/SSE for Hermes
```

For Hermes Agent, add to `~/.hermes/config.yaml`:
```yaml
mcp_servers:
  psamvault:
    url: "http://127.0.0.1:8433/sse"
    enabled: true
```
