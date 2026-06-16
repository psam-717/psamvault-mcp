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

## Tools you need
| Tool | Purpose |
|------|---------|
| `search_vault_tools` | Discover which tool to use — call this first with relevant keywords |
| `list_vault_sites` | List all stored site names (no passwords) |
| `check_credential_exists` | Verify a credential exists for a specific site |
| `browser_login` | Open a real browser and log into the site. This is **always** the right tool for login |

## Workflow

### Step 1: Call search_vault_tools("login")
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
"""

_HOW_TO_DISCOVER_CREDS = """\
# How to discover what credentials are stored

## Goal
Find out which credentials exist in the user's psamvault vault and report the
site names and username hints.

## Prerequisites
- The user must be logged in to psamvault (`psamvault login` in their terminal).

## Tools you need
| Tool | Purpose |
|------|---------|
| `list_vault_sites` | List all stored site names with username hints |
| `check_credential_exists` | Check if a specific site has a credential |

## Workflow

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

## Tools you need
| Tool | Purpose |
|------|---------|
| `check_credential_exists` | Verify the credential exists and see the username hint |
| `get_username_for_site` | Retrieve the username |

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

## Tool reference

| Tool | Purpose | When to call |
|------|---------|--------------|
| `get_version` | Check psamvault-mcp version | Anytime — no login needed |
| `search_vault_tools` | Discover which tool to use | **First** — when unsure what tool fits |
| `list_vault_sites` | List stored sites with hints | When user asks "what do I have" |
| `check_credential_exists` | Check if a site has a credential | Before any credential-dependent tool |
| `get_username_for_site` | Get username only | When username is needed in a form/API |
| `browser_login` | Full login via browser | **Always** for login/authenticate requests |
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