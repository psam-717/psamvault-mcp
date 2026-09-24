# Plan: Playwright Browser Login Integration for psamvault-mcp

## Problem
The current MCP only supports API-based credential use (via the backend proxy). It cannot log into websites via a browser. We want psamvault-mcp to handle the entire browser login itself using its own Playwright instance — so the plaintext credentials never leave the MCP process and are never exposed to Goose.

## Approach
Add a new `browser_login` MCP tool that:
1. Shows a consent prompt to the user (mandatory gate)
2. Decrypts credentials locally using the VEK (existing `_decrypt_site_credential`)
3. Launches its own **headed** (visible) Playwright browser
4. Navigates to the login URL
5. Fills the username and password fields (CSS selectors provided by Goose)
6. Clicks the submit button
7. Waits for navigation to complete
8. Attempts to detect login failure messages on the resulting page
9. Returns `{ success, url, title, error_text }` to Goose — no passwords, ever

## Key design decision — async
Playwright's Python API is async. `browser_login` must be `async def`. Since `handle_call_tool` in `main.py` is already `async`, it can simply `await` the new tool directly. All existing sync tools remain unchanged.

## Files to change

### 1. `pyproject.toml`
- Add `playwright>=1.40.0` to the `dependencies` list

### 2. `requirements.txt`
- Add `playwright` with its pinned version

### 3. `mcp_server/tools.py`
- Add `async def browser_login(site_name, login_url, username_selector, password_selector, submit_selector)`:
  - Login check
  - Consent gate (reuse `consent.request_consent`)
  - Call `_decrypt_site_credential(site_name)` — credentials stay in memory only
  - `async with async_playwright() as p:` → `browser = await p.chromium.launch(headless=False)`
  - `page.fill(username_selector, credentials["username"])`
  - `page.fill(password_selector, credentials["password"])`
  - `page.click(submit_selector)` + `page.wait_for_load_state("networkidle")`
  - Attempt to read common error selectors (`[role=alert]`, `.error`, `#error`) — if found, capture text
  - Return `{ "success": bool, "url": str, "title": str, "error_text": str | None }`
  - Close browser in `finally` block

### 4. `mcp_server/main.py`
- Add new `Tool` definition to `TOOL_DEFINITIONS` for `browser_login`:
  - Required: `site_name` only. `login_url` and the selectors are optional — the flow discovers the
    sign-in page and its fields when they are omitted, which is what the tool's own description says.
- Add `elif name == "browser_login": result = await tools.browser_login(...)` in `handle_call_tool`

## Files NOT changing
- `session.py` — no changes needed
- `api_client.py` — no changes needed
- Backend — no changes needed
- CLI — no changes needed

## Post-implementation steps
After code changes:
1. `pipx install . --force` — reinstall the package with playwright dependency
2. `playwright install chromium` — download the Chromium browser binary inside the pipx venv

## Implementation Todos
- [ ] Add `playwright>=1.40.0` to `pyproject.toml` dependencies
- [ ] Add `playwright` pin to `requirements.txt`
- [ ] Implement `async def browser_login()` in `mcp_server/tools.py`
- [ ] Register `browser_login` Tool definition in `mcp_server/main.py` TOOL_DEFINITIONS
- [ ] Add `await tools.browser_login(...)` routing in `handle_call_tool` in `mcp_server/main.py`
- [ ] Run `pipx install . --force` and `playwright install chromium`
