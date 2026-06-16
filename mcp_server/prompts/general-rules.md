---
name: general-rules
description: Security rules, error handling guidance, and complete tool reference for psamvault
type: agent-skill
---

# General rules for using psamvault

## Security rules — these are not suggestions
These rules are security requirements. Violating them could expose credentials.

### Rule 1: No shell commands for credentials
Never run `psamvault get`, `psamvault show`, or any psamvault CLI command.
Do not read credential files from the filesystem.
The only permitted way to access credentials is through the MCP tools.

### Rule 2: browser_login for all login requests
Use `browser_login` for: logging in, signing in, authenticating, signing on,
accessing an account, or entering credentials for a website.
This includes trigger phrases like "log into", "log in to", "sign into",
"sign in to", "sign on to", "authenticate to", "access my account on",
"enter my password for", "fill in credentials for", or "login to".

### Rule 3: Discover first, then act
Always call `search_vault_tools` first when you are unsure which tool to use.
If you are unsure which sites exist, call `list_vault_sites`.
If you are unsure whether a credential exists, call `check_credential_exists`.

### Rule 4: Credential values are never returned to you
- `browser_login` fills credentials inside a browser — you never see them.
- `get_username_for_site` only returns the username, never the password.
- `list_vault_sites` only returns site names and username hints.

## Error handling

### "Not logged in"
The session file or keychain entry is missing. Tell the user:
"Please run `psamvault login` in your terminal, then ask me again."

### CAPTCHA during browser_login
1. Inform the user a CAPTCHA was detected.
2. If a `captcha_screenshot` path is provided, tell them to inspect it.
3. Ask them to solve the CAPTCHA in the open browser window.
4. Ask them to click the Sign in / Login button manually after solving it.

### Browser timeout
If `browser_login` times out (600 seconds), the browser may be stuck on
a slow or JavaScript-heavy page. Suggest retrying with a higher `timeout_ms`
or checking for network issues.

### Unknown site
If `check_credential_exists` returns `exists: false`, the site isn't in the vault.
The user must add it via `psamvault add` before you can use it.

## Tool reference

| Tool | Purpose | When to call |
|------|---------|--------------|
| `get_version` | Check psamvault-mcp version | Anytime — no login needed |
| `search_vault_tools` | Discover which tool to use | **First** — when unsure what tool fits |
| `list_vault_sites` | List stored sites with hints | When user asks "what do I have" |
| `check_credential_exists` | Check if a site has a credential | Before any credential-dependent tool |
| `get_username_for_site` | Get username only | When username is needed in a form/API |
| `browser_login` | Full login via browser | **Always** for login/authenticate requests |
