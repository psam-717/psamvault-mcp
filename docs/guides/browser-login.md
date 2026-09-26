---
title: Browser login
description: browser_login — the visible-browser model, when to use it instead of injecting, and its limits.
order: 50
---

# Browser login

`browser_login` is how an agent logs a user into a **website**. It is the counterpart to
[credential injection](./credential-injection.md), which covers APIs and CLI tools.

## What it does

From the tool description, verbatim:

> Open a visible browser and securely log into a site using a stored psamvault credential. Playwright
> navigates from the site homepage, finds the sign-in link, and handles the full login flow —
> including multi-step flows (e.g., 'Continue with Email' → email → Next → password → submit). Uses
> semantic locators (get_by_role, get_by_label) that work with Shadow DOM, React, and Vue apps. Saves
> the browser session after a successful login so it can be reused on subsequent calls. The credential
> is NEVER returned to you — psamvault fills the fields directly inside its own browser.

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
```

Only `site_name` is required. `login_url`, `username_selector`, `password_selector`,
`submit_selector`, and `timeout_ms` are all optional.

## When to use it instead of injecting

Use `browser_login` for anything phrased as logging in: "log into", "log in to", "sign into",
"sign in to", "sign on to", "authenticate to", "access my account on", "enter my password for",
"fill in credentials for", or "login to". That is **always** the right tool for a login request.

Reach for [credential injection](./credential-injection.md) instead when the target is an API
(`use_credential`) or a CLI tool (`run_with_credential`). The distinction is the destination, not the
difficulty: a browser login exists to produce an authenticated **browser session** the user then
works in; injection exists to produce an authenticated **request or command** whose output the agent
reads.

Recommended sequence (from the shipped `how-to-login` prompt):

1. `search_vault_tools("login")` — confirm `browser_login` is available.
2. `list_vault_sites()` — see what the user has stored; check the requested site appears.
3. `check_credential_exists(site_name="site.example.com")` — recommended; `exists: false` means the
   credential isn't stored and the user must add it first.
4. `browser_login(site_name="site.example.com")`.

## The visible-browser model

- **The browser is real and visible (headed).** It is Chromium driven by Playwright, launched
  `headless=False`, so the user can watch the login and take over.
- **Single process, in-process.** The MCP server manages one singleton Playwright Chromium instance
  in the same process as the server — no subprocess daemon and no internal HTTP proxy. That
  eliminates the fragile 3-process chain (MCP → CLI daemon → browser) that caused connection errors
  with certain MCP clients (e.g. Goose's `ECONNREFUSED` on internal proxy ports).
- **Auto-recovery.** If the browser crashes during use, the next `browser_login` call launches a new
  instance.
- **The credential never leaves the browser.** psamvault decrypts the site credential locally and
  types it into the page's own fields. It is never returned in the result.
- **Session reuse.** After a successful login the browser session is saved and reused on subsequent
  calls to the same site, and the auto-discovered login page URL is persisted back to the vault entry.

## Limits and caveats

- **Site credentials only.** `browser_login` uses a stored **site** credential (the vault entry's
  username/password). It is not a way to use an API key against a website; that is `use_credential`.
- **The site must exist in the vault.** If it does not, the answer is to tell the user — never to
  substitute a different `site_name`.
- **Playwright Chromium must be installed**, or the browser cannot launch:
  `playwright install chromium`.
- **Never invent CSS selectors.** Pass `login_url` / `username_selector` / `password_selector` /
  `submit_selector` only if the user supplies them exactly; auto-detection is the default and guessed
  selectors are a documented "what not to do". Never modify a `login_url` you receive — pass it
  as-is.
- **CAPTCHA pauses automation.** When `captcha_detected` is `true`, the tool pauses; inform the user,
  and when `captcha_screenshot` is not null tell them a screenshot was saved to that path so they can
  inspect it. The user solves the CAPTCHA in the open browser window and clicks Sign in / Login
  manually.
- **Per-step detection timeout.** `timeout_ms` defaults to **8000** milliseconds and is a *per-step*
  detection timeout — increase it for slow or JavaScript-heavy sites.
- **Whole-call timeout.** If `browser_login` times out (600 seconds), the browser may be stuck on a
  slow or JavaScript-heavy page. Suggest retrying with a higher `timeout_ms` or checking for network
  issues.
- **Failure is reported, not hidden.** Check `failed_at` to see which step failed (for example
  `login_url_discovery`, `form_fill`, `submit`) and `hint` for a recovery suggestion, and relay the
  hint to the user. Do not work around a failure with shell commands or by reading files.
- **A CAPTCHA or a failed form is not a credential problem.** Never escalate a browser-login failure
  into fetching a secret another way.

## What it returns

| Key | Meaning |
|---|---|
| `success` | Whether the login completed. |
| `message` | A user-facing message. When `success` is `true`, **always relay it to the user** — it contains instructions. |
| `captcha_detected` | Whether a CAPTCHA paused the flow. |
| `captcha_screenshot` | Path to a saved CAPTCHA screenshot, or null. Tell the user the path when it is set. |
| `login_page_screenshot` | Path to a saved screenshot of the login page, or null. |
| `steps_count` | How many steps the flow took. |
| `failed_at` | Which step failed, when it did. |
| `url` / `title` | The page the flow ended on. |
| `error_text` | Text of a detected on-page error, or null. |
| `hint` | A recovery suggestion to relay to the user. |

Read the key the tool actually returns — `url`. (Tool descriptions older than 0.5.4 called it
`final_url`; the flow never did.)

## See also

- [Tool reference](./../reference/tools.md#browser_login) — the parameter list and defaults.
- [`mcp_server/prompts/how-to-login.md`](../../mcp_server/prompts/how-to-login.md) — the step-by-step
  agent workflow, including edge cases, shipped with the server.
- [PLAYWRIGHT_INTEGRATION.md](../../PLAYWRIGHT_INTEGRATION.md) — the original design plan behind this
  tool. Note that its parameter list predates the shipped auto-discovery behaviour: in the code only
  `site_name` is required.
- [Credential injection](./credential-injection.md) — for APIs and CLI tools.
