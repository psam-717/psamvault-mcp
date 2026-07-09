"""
MCP Prompt templates for AI agents connected to psamvault.

These prompts serve as structured guidance — like a "skill" — that an agent
can load at runtime via the MCP Prompts capability (list_prompts / get_prompt).
They tell the agent which tool to call, in what order, and how to handle
edge cases, preventing hallucination and wrong tool choices.

Each entry in PROMPT_REGISTRY maps a name → {name, description, content}.
"""

PROMPT_REGISTRY: dict[str, dict[str, str]] = {}

_HOW_TO_LOGIN = """\
# How to log a user into a website

## Goal
Use a stored credential to log the user into a website. The credential value is
**never returned to you** — psamvault fills the fields directly inside its own
browser process.

## Prerequisites
- The user must be logged in to psamvault. If not, tell them to run:
  `psamvault login` in their terminal and then ask you again.

## Tools you need (🔐 Site Authentication group)

| Tool | Purpose | Group |
|------|---------|-------|
| `search_vault_tools` | Discover which tool to use — call this first | 🛠 Entry & Orientation |
| `list_vault_sites` | List all stored site names (no passwords) | 🔐 Site Authentication |
| `check_credential_exists` | Verify a credential exists for a specific site | 🔐 Site Authentication |
| `browser_login` | Open a real browser and log into the site. This is **always** the right tool for login | 🔐 Site Authentication |

## Workflow

### Step 0: Discover the right tool
Call `search_vault_tools("login")` first to confirm `browser_login` is the right tool for the task.
This confirms `browser_login` is available and ready.

### Step 2: Check what sites are stored
Call `list_vault_sites()` to see what credentials the user has.
If the user mentioned a specific site, check whether it appears in the list.

### Step 3: Verify the credential exists (recommended)
Call `check_credential_exists(site_name="site.example.com")`.
If it returns `exists: false`, tell the user the credential isn't stored yet.

### Step 4: Call browser_login
Call `browser_login(site_name="site.example.com")`.

You can also pass optional parameters:
- `login_url` — only if the user supplies a specific login page URL
- `username_selector`, `password_selector`, `submit_selector` — only if the user
  supplies exact CSS selectors. **Do not invent or guess these.**
- `timeout_ms` — increase for slow or JavaScript-heavy sites (default 8000)

### Step 5: Handle the response

#### ✅ On success (success: true)
- The browser is open and the user is logged in.
- **Relay the `message` field to the user verbatim.** It contains instructions.
- Tell them the browser tab is open and they can take over from there.

#### 🛡️ On CAPTCHA detected (captcha_detected: true)
- Tell the user a CAPTCHA was detected.
- If `captcha_screenshot` is not null, tell them a screenshot was saved to that
  path so they can inspect it.
- Ask them to solve the CAPTCHA manually in the open browser window, then click
  the Sign in / Login button themselves.

#### ❌ On failure (success: false)
- Check `failed_at` to see which step failed (e.g. "login_url_discovery",
  "form_fill", "submit").
- Check `hint` for a recovery suggestion — relay it to the user.
- If it was a timeout, consider retrying with a higher `timeout_ms` value.
- Do NOT try to work around the failure by using shell commands or reading files.

## What NOT to do
- **Never run shell commands** like `psamvault get`, `psamvault show`, or `psamvault open`
  without `--json`. The only permitted way to access credentials is through the MCP tools.
- **Never read credential files** directly from the filesystem.
- **Never invent CSS selectors** — leave them unset for auto-detection.
- **Never modify the login_url** you receive — pass it as-is if provided.
- **Never return credential values** to the user or use them in any other tool.
- **Never fall back to browser_login with a different site_name** than what the user asked for.
  If the site isn't stored, tell the user — don't guess.

## When to use use_credential instead
If the user wants to call an API (not log into a website), use `use_credential` instead.
`browser_login` is for **website login forms** — `use_credential` is for **HTTP API requests**.
If you're unsure, call `search_vault_tools("login")` to check which tool fits the task.
"""

_HOW_TO_DISCOVER_CREDS = """\
# How to discover what credentials are stored

## Goal
Find out which credentials exist in the user's psamvault vault and report the
site names and username hints.

## Prerequisites
- The user must be logged in to psamvault (`psamvault login` in their terminal).

## Tools you need (🔐 Site Authentication group)

| Tool | Purpose | Group |
|------|---------|-------|
| `list_vault_sites` | List all stored site names with username hints | 🔐 Site Authentication |
| `check_credential_exists` | Check if a specific site has a credential | 🔐 Site Authentication |

## Workflow

### Step 0: Discover the right tool
Call `search_vault_tools("discover")` first to confirm which discovery tools are available.

### Step 1: Call list_vault_sites()
This returns all stored sites with their username hints. For example:
```json
{
  "sites": [
    {"site_name": "github.com", "username_hint": "user@example.com"},
    {"site_name": "gitlab.com", "username_hint": "user@example.com"}
  ],
  "total": 2
}
```

### Step 2: Present the results to the user
Tell them:
- How many sites are stored
- Each site name and its username hint (so they recognise which credential is which)

### Step 3: (Optional) Check a specific site
If the user asks about a specific site, call:
`check_credential_exists(site_name="site.example.com")`
This returns whether the credential exists and the username hint.

## What NOT to do
- **Never return passwords** — `list_vault_sites` never shows them.
- **Never call browser_login** unless the user explicitly asks to log in.
"""

_HOW_TO_GET_USERNAME = """\
# How to retrieve a username for a site

## Goal
Return just the username (not the password) stored for a given site. The username
is returned to you so you can fill it in a form field or include it in an API
request body. The password is **never** returned.

## Prerequisites
- The user must be logged in to psamvault (`psamvault login` in their terminal).

## Tools you need (🔐 Site Authentication group)

| Tool | Purpose | Group |
|------|---------|-------|
| `search_vault_tools` | Discover which tool to use — call this first | 🛠 Entry & Orientation |
| `check_credential_exists` | Verify the credential exists and see the username hint | 🔐 Site Authentication |
| `get_username_for_site` | Retrieve the username | 🔐 Site Authentication |

## Workflow

### Step 1: Verify the credential exists
Call `check_credential_exists(site_name="site.example.com")`.
If it returns `exists: false`, tell the user the credential isn't stored yet.

### Step 2: Call get_username_for_site
Call `get_username_for_site(site_name="site.example.com")`.

### Step 3: Handle the response

#### ✅ On success
The response includes `username` — use it only for the task the user requested.

#### ❌ On failure
If `error` is returned, check the message and tell the user.

## What NOT to do
- **Never ask for or try to retrieve a password** — use `browser_login` instead.
- **Never return the username** in a format that could be confused with a password.
- **Never cache or store** the username after the task is complete.
"""

_GENERAL_RULES = """\
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
- `bearer_token` — Authorization: Bearer *** (default)
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

### "Not logged in" / session timed out
The session file or keychain entry is missing or expired. Tell the user:
"Please run `psamvault login` in your terminal, then ask me again."
Do not try to refresh the session by reading vault files or running
`psamvault get`. Optionally they can try `psamvault list` / `psamvault whoami`
first; if those fail, login is required.

### MCP tools missing or server will not start
Do not invent shell workarounds that print secrets. Follow the install playbook
in the repo docs: `docs/troubleshooting/MCP-INSTALL-AND-CONNECT.md`.

Typical causes and fixes:
- **Missing binary in config** → `pipx install psamvault-mcp`; point config at the absolute path under `~/.local/bin/`
- **Broken system Python shim on PATH** (`ModuleNotFoundError: pydantic`) → use absolute pipx path
- **Corrupt pipx metadata** → delete `~/pipx/venvs/psamvault-mcp` and reinstall
- **PYTHONPATH contamination** → set `env.PYTHONPATH=""` on the MCP server entry
- **Config fixed but tools still absent** → restart/reload the agent session
- **Smoke-test hang** → bare `psamvault-mcp` waits on stdio; use `--version` or `--help` instead

### "Consent GUI unavailable"
The user is on a headless system without a graphical display.
They need to run the command in a GUI environment.

### CAPTCHA during browser_login
1. Inform the user a CAPTCHA was detected.
2. If a `captcha_screenshot` path is provided, tell them to inspect it.
3. Ask them to solve the CAPTCHA in the open browser window.
4. Ask them to click the Sign in / Login button manually after solving it.

### Browser timeout
If `browser_login` times out after 600 seconds, the browser may be stuck on
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

## Tool reference (grouped by category)

### 🛠  Entry & Orientation
*Always start here — no credential access needed.*

| Tool | Purpose | When to call |
|------|---------|--------------|
| `get_version` | Check psamvault-mcp version | Anytime — no login needed |
| `search_vault_tools` | Discover which tool to use | **First** — when unsure what tool fits |

### 🔐  Site Authentication
*End-to-end: discover, check, and log into websites.*

| Tool | Purpose | When to call |
|------|---------|--------------|
| `list_vault_sites` | List stored sites with hints | When user asks "what do I have" |
| `list_api_keys` | List stored API key names | When user asks "what API keys do I have" |
| `check_credential_exists` | Check if a site has a credential | Before any credential-dependent tool |
| `get_username_for_site` | Get username only | When username is needed in a form/API |
| `browser_login` | Full login via browser | **Always** for login/authenticate requests |

### 🔑  API Key Operations
*All tools that deal with API keys: discover, use, inject, and protect.*

| Tool | Purpose | When to call |
|------|---------|--------------|
| `use_credential` | Make authenticated HTTP/API requests | **Always** for API calls needing auth |
| `run_with_credential` | Run CLI command with credential injected | When a CLI tool needs a credential (env/stdin) |
| `scan_and_protect` | Scan .env files for exposed secrets | **First** when working in a project with .env files |
| `capture_stripe_credentials` | Capture Stripe Projects credentials | **Immediately** after `stripe projects add <provider>` |
"""

_HOW_TO_USE_API_CREDENTIAL = """\
# How to use a stored credential for API requests

## Goal
Make an authenticated HTTP request using a credential stored in psamvault
without ever seeing the plaintext credential value. The MCP server looks up
API key entries first, falls back to vault entries, decrypts the credential
locally, injects it into the HTTP request, and returns only the response.

## Prerequisites
- The user must be logged in to psamvault (`psamvault login` in their terminal).
- The credential must already be stored (via `psamvault add`, `scan_and_protect`,
  or `capture_stripe_credentials`).

## Tools you need (🔑 API Key Operations group)

| Tool | Purpose | Group |
|------|---------|-------|
| `search_vault_tools` | Discover which tool to use — call this first | 🛠 Entry & Orientation |
| `check_credential_exists` | Verify the credential exists before calling use_credential | 🔐 Site Authentication |
| `use_credential` | Make the authenticated HTTP/API request | 🔑 API Key Operations |

## Workflow

### Step 1: Verify the credential exists
Call `check_credential_exists(site_name="my-api-key")`.
If it returns `exists: false`, the credential isn't stored yet.

### Step 2: Call use_credential
Call `use_credential(
    site_name="my-api-key",
    target_url="https://api.example.com/data",
    method="GET",
    inject_as="bearer_token",
)`

### Step 3: Handle the response

#### ✅ On success (success: true)
- The response `data` contains the API response.
- Use the data for what the user requested.
- `status_code` tells you the HTTP status.

#### ❌ On failure (error in response)
- Check the `error` message and relay it to the user.
- If the credential wasn't found, suggest adding it via `psamvault add`.
- If the target URL returned an error, report the status code.

## Examples

### Bearer token (default)
```
use_credential("openai.com", "https://api.openai.com/v1/models", "GET")
```
The credential is injected as `Authorization: Bearer sk-...`.

### Custom header (API key header)
```
use_credential("my-api", "https://api.example.com/data",
               inject_as="api_key_header", header_name="X-API-Key")
```
The credential is injected as `X-API-Key: abc123...`.

### Basic auth
```
use_credential("my-api", "https://api.example.com/auth",
               inject_as="basic_auth")
```
The credential is injected as `Authorization: Basic <base64>`.

### Token-efficient field filtering
```
use_credential("github", "https://api.github.com/user",
               fields=["login", "id", "public_repos"])
```
Only returns the specified fields — saves tokens.

## What NOT to do
- **Never read the credential value** from the response — it's never returned.
- **Never use browser_login** for API requests — use use_credential instead.
- **Never expose the credential** in logs, messages, or any other output.
"""

_HOW_TO_SCAN_AND_PROTECT = """\
# How to scan a project for exposed secrets and protect them

## Goal
Scan a project directory for `.env` files containing exposed API keys,
passwords, tokens, or database URLs. Each detected secret is encrypted
with the VEK, stored in the psamvault API key store, and the plaintext
value is replaced with a `psamvault:<KEY_NAME>` placeholder so the agent
never reads the raw value.

## Prerequisites
- The user must be logged in to psamvault (`psamvault login` in their terminal).

## Tools you need (🔑 API Key Operations group)

| Tool | Purpose | Group |
|------|---------|-------|
| `search_vault_tools` | Discover which tool to use — call this first | 🛠 Entry & Orientation |
| `scan_and_protect` | Scan and protect exposed secrets | 🔑 API Key Operations |

## Workflow

### Step 1: Scan the project
Call `scan_and_protect()` with no arguments to scan the current directory,
or pass a specific `project_dir`:
```
scan_and_protect(project_dir="/path/to/project")
```

### Step 2: Review the results
The tool returns:
- `files_scanned` — which `.env` files were found
- `secrets_found` — how many unprotected secrets were found
- `captured` — list of secrets that were encrypted and stored
- `captured_count` — total captured
- `files_modified` — which files were updated with placeholders
- `files_not_gitignored` — files that should be in `.gitignore`

### Step 3: Handle edge cases

#### No secrets found
If `secrets_found` is 0, the project is clean. No action needed.

#### Files not gitignored
If `files_not_gitignored` is non-empty, suggest the user add `.env` to
their `.gitignore` to prevent accidental commits.

#### Errors during capture
If `errors` is non-empty, some secrets couldn't be stored. Report the
errors to the user.

## What NOT to do
- **Never read the plaintext .env file** directly — let scan_and_protect handle it.
- **Never share the original secret values** with the user or any other tool.
- **Never skip scan_and_protect** when working in a project with .env files —
  it's a one-time safety measure.

## After scan_and_protect
The captured secrets can be used with `use_credential` by their vault name:
`env/<filename>/<KEY_NAME>`. For example, if `OPENAI_API_KEY` was captured
from `.env`, it's stored as `env/.env/OPENAI_API_KEY`.
"""

_HOW_TO_CAPTURE_STRIPE = """\
# How to capture Stripe Projects provisioned credentials

## Goal
After an agent provisions infrastructure via `stripe projects add <provider>`,
the resulting credentials (database URLs, API keys, auth tokens) land in the
project's `.env` file as plaintext. This guide captures them into psamvault,
encrypts them, and replaces the plaintext values with placeholders.

## Prerequisites
- The user must be logged in to psamvault (`psamvault login` in terminal).
- The Stripe CLI must be installed, authenticated, and a Stripe project
  must be active in the current directory.
- The `stripe projects add <provider>` command must have already been run.

## Tools you need (🔑 API Key Operations group)

| Tool | Purpose | Group |
|------|---------|-------|
| `search_vault_tools` | Discover which tool to use — call this first | 🛠 Entry & Orientation |
| `capture_stripe_credentials` | Capture Stripe-provisioned credentials | 🔑 API Key Operations |

## Workflow

### Step 1: Run stripe projects add (already done by agent)
The agent should have run `stripe projects add neon/postgres` or similar
to provision a resource. This writes credentials to `.env`.

### Step 2: Call capture_stripe_credentials
Call `capture_stripe_credentials(provider="neon")` **immediately** after
provisioning, before any other agent action reads the `.env` file.

You can also pass `project_dir` if the project isn't the current directory:
```
capture_stripe_credentials(
    provider="supabase",
    project_dir="/path/to/project"
)
```

### Step 3: Review the results
The tool returns:
- `success` — whether the operation succeeded
- `captured` — list of credentials that were captured
- `captured_count` — total captured
- `files_modified` — which `.env` files were updated
- `message` — human-readable summary

### Step 4: Use the captured credentials
The captured credentials are stored as `stripe/<provider>/<KEY_NAME>`.
Use them with `use_credential`:
```
use_credential("stripe/neon/NEON_DATABASE_URL", ...)
```

## Dry run mode
Call `capture_stripe_credentials(provider="neon", dry_run=True)` to preview
what would be captured without actually modifying anything. This is useful
for verification before making changes.

## Error handling

### Stripe CLI not found
If the tool fails with "Stripe CLI not found", tell the user to install it:
https://stripe.com/docs/stripe-cli

### No .env file
If no `.env` file is found after the pull, Stripe Projects may not have been
initialised. The user needs to run `stripe projects use` first.

### Not a Stripe project
If the Stripe CLI exits with an error, the directory may not be a Stripe
project. Tell the user to run `stripe projects use` in the directory.

## What NOT to do
- **Never read the plaintext `.env` file** directly — let the tool handle it.
- **Never skip the capture step** — credentials in `.env` are a security risk.
- **Never expose the raw credential values** in your response.
"""

# ── Build the registry ──────────────────────────────────────────────────────

PROMPT_REGISTRY["how-to-login"] = {
    "name": "how-to-login",
    "description": "Guide for logging into a website using stored credentials — step-by-step workflow with edge case handling",
    "content": _HOW_TO_LOGIN.strip(),
}

PROMPT_REGISTRY["how-to-discover-creds"] = {
    "name": "how-to-discover-creds",
    "description": "Guide for discovering what credentials are stored in the vault — list sites and check for specific ones",
    "content": _HOW_TO_DISCOVER_CREDS.strip(),
}

PROMPT_REGISTRY["how-to-get-username"] = {
    "name": "how-to-get-username",
    "description": "Guide for retrieving a stored username (not password)",
    "content": _HOW_TO_GET_USERNAME.strip(),
}

PROMPT_REGISTRY["general-rules"] = {
    "name": "general-rules",
    "description": "Security rules, error handling guidance, and complete tool reference for psamvault",
    "content": _GENERAL_RULES.strip(),
}

PROMPT_REGISTRY["how-to-use-api-credential"] = {
    "name": "how-to-use-api-credential",
    "description": "Guide for making authenticated HTTP/API requests using stored credentials",
    "content": _HOW_TO_USE_API_CREDENTIAL.strip(),
}

PROMPT_REGISTRY["how-to-scan-and-protect"] = {
    "name": "how-to-scan-and-protect",
    "description": "Guide for scanning project .env files for exposed secrets and protecting them with psamvault",
    "content": _HOW_TO_SCAN_AND_PROTECT.strip(),
}

PROMPT_REGISTRY["how-to-capture-stripe"] = {
    "name": "how-to-capture-stripe",
    "description": "Guide for capturing Stripe Projects provisioned credentials into psamvault",
    "content": _HOW_TO_CAPTURE_STRIPE.strip(),
}