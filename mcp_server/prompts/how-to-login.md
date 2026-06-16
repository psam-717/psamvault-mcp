---
name: how-to-login
description: Guide for logging into a website using stored credentials — step-by-step workflow with edge case handling
type: agent-skill
---

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
- If it was a timeout, try retrying with a higher `timeout_ms` value.
- Do NOT try to work around the failure by using shell commands or reading files.

## What NOT to do
- **Never run shell commands** like `psamvault get`, `psamvault show`, or `psamvault open`.

- **Never read credential files** directly from the filesystem.
- **Never invent CSS selectors** — leave them unset for auto-detection.
- **Never modify the login_url** you receive — pass it as-is if provided.
- **Never return credential values** to the user or use them in any other tool.
- **Never fall back to browser_login with a different site_name** than what the user asked for.
  If the site isn't stored, tell the user — don't guess.