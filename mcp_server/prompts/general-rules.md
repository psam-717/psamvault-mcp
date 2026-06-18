---
name: general-rules
description: Security rules, error handling guidance, and complete tool reference for psamvault
type: agent-skill
---

# General rules for using psamvault

## Security rules — these are not suggestions
These rules are security requirements. Violating them could expose credentials.

### Rule 1: No shell commands for credentials
Never run `psamvault get`, `psamvault show`, `psamvault open`,
or any psamvault CLI command. Do not read credential files from the filesystem.
The only permitted way to access credentials is through the MCP tools.

### Rule 2: browser_login for all website login requests
Use `browser_login` for: logging in, signing in, authenticating, signing on,
accessing an account, or entering credentials for a website.
This includes trigger phrases like "log into", "log in to", "sign into",
"sign in to", "sign on to", "authenticate to", "access my account on",
"enter my password for", "fill in credentials for", or "login to".

### Rule 3: use_credential for API requests
Use `use_credential` for all authenticated HTTP/API requests. It looks up API
key entries first, then falls back to vault (site password) entries if no API
key is found. The credential value is **never returned** — only the HTTP
response from the target is returned to you.

Supported injection modes:
- `bearer_token` — Authorization: Bearer <key> (default)
- `api_key_header` — <header_name>: <key> (requires `header_name`)
- `basic_auth` — Authorization: Basic base64(<key>:<key>)

### Rule 4: Capture Stripe Projects credentials immediately
After running `stripe projects add <provider>`, call
`capture_stripe_credentials(provider="<provider>")` **immediately** to encrypt
the provisioned credentials into psamvault and replace them with placeholders.
This prevents the agent from reading the plaintext `.env` file.

### Rule 5: Scan and protect existing projects
When working in a project directory that has `.env` files, call
`scan_and_protect()` to detect and encrypt any exposed secrets. This is a
one-time safety check before the agent reads any files in the project.

### Rule 6: Discover first, then act
Always call `search_vault_tools` first when you are unsure which tool to use.
If you are unsure which sites exist, call `list_vault_sites`.
If you are unsure whether a credential exists, call `check_credential_exists`.

### Rule 7: Credential values are never returned to you
- `browser_login` fills credentials inside a browser — you never see them.
- `use_credential` injects credentials into HTTP requests — only the response
  comes back to you.
- `get_username_for_site` only returns the username, never the password.
- `list_vault_sites` only returns site names and username hints.

## Error handling

### "Not logged in"
The session file or keychain entry is missing. Tell the user:
"Please run `psamvault login` in your terminal, then ask me again."

### "Consent GUI unavailable"
The user is on a headless system without a graphical display.
They need to run the command in a GUI environment.

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

### Stripe CLI not found
If `capture_stripe_credentials` returns `success: false` with a message about
Stripe CLI not found, tell the user to install the Stripe CLI from
https://stripe.com/docs/stripe-cli and authenticate.

### scan_and_protect returns nothing found
If `scan_and_protect` returns 0 secrets found, the project is clean —
no action needed. If it returns `files_not_gitignored`, suggest the user
add `.env` to their `.gitignore`.

## Tool reference

| Tool | Purpose | When to call |
|------|---------|--------------|
| `get_version` | Check psamvault-mcp version | Anytime — no login needed |
| `search_vault_tools` | Discover which tool to use | **First** — when unsure what tool fits |
| `list_vault_sites` | List stored sites with hints | When user asks "what do I have" |
| `list_api_keys` | List stored API key names | When user asks "what API keys do I have" |
| `check_credential_exists` | Check if a site has a credential | Before any credential-dependent tool |
| `get_username_for_site` | Get username only | When username is needed in a form/API |
| `browser_login` | Full login via browser | **Always** for login/authenticate requests |
| `use_credential` | Make authenticated HTTP/API requests | **Always** for API calls needing auth |
| `scan_and_protect` | Scan .env files for exposed secrets | **First** when working in a project with .env files |
| `capture_stripe_credentials` | Capture Stripe Projects credentials | **Immediately** after `stripe projects add <provider>` |
