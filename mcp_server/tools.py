"""
psamvault MCP tools.
 
These are the tools exposed to AI agents. Each tool is designed so that
the agent can orchestrate credential use without ever seeing the plaintext
value. The consent gate in each tool ensures the user approves every access.
 
Key architecture note:
  All sensitive session values (VEK, tokens, kdf_salt) are stored in the
  OS keychain (macOS Keychain, Windows Credential Manager, Linux Secret
  Service) by the psamvault CLI at login time. The MCP server reads the
  VEK from the keychain on every credential access via session.get_vek().
  No key derivation is needed here — the CLI did all the derivation work
  (HMAC → PBKDF2 → AES-GCM-decrypt) at login time.
 
Token-efficiency changes:
  - use_credential: accepts `fields` list to trim response before it
    reaches the model context. Works on both dict and list-of-dict payloads.
  - browser_login: _result() now returns a concise summary dict instead of
    the full steps_completed list. steps_count (int) replaces steps_completed
    (list) in the returned payload, cutting token cost from ~20 tokens/step
    to a fixed ~15 tokens total. failed_at and hint are preserved so the
    agent can still retry intelligently on failure.
"""


import asyncio
import contextlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from mcp_server import api_client, consent
from mcp_server.consent import ConsentGUIUnavailableError
from mcp_server.crypto import decrypt_credentials
from mcp_server.log import get_logger
from mcp_server.session import (
    get_access_token,
    get_vek,
    is_logged_in
)

logger = get_logger()

# ── Active browser tracking for graceful shutdown ──────────────────────────
_ACTIVE_BROWSERS: set = set()


async def close_all_browsers() -> None:
    """Force-close all tracked Chromium instances.

    Called from main.py when the MCP server shuts down so that orphaned
    browser processes are cleaned up if the client disconnects while a
    browser_login tool call is still active.
    """
    for b in list(_ACTIVE_BROWSERS):
        try:
            await b.close()
        except Exception:
            pass
    _ACTIVE_BROWSERS.clear()


# helper - fetch and decrypt a vault entry using the session VEK

async def _decrypt_site_credential(site_name: str) -> dict:
    """
    Fetch the encrypted vault entry from the API and decrypt it locally
    using the VEK from the OS keychain.

    The VEK is read fresh from the keychain on every call so that
    if the user logs out mid-session the VEK is no longer accessible.

    Returns:
        {"username": str, "password": str, "notes": str}
        Held in memory only — never logged or persisted.

    Raises:
        RuntimeError: If not logged in or VEK not in keychain.
        cryptography.exceptions.InvalidTag: If decryption fails.
    """
    access_token = get_access_token()
    entry = await api_client.get_vault_entry(access_token, site_name)

    vek = get_vek() 

    return decrypt_credentials(
        vek=vek,
        encrypted_blob=entry["encrypted_blob"],
        iv=entry["iv"]
    )


async def _request_consent_async(
    site_name: str,
    target_url: str,
    inject_as: str,
    agent_description: str = "AI agent via psamvault MCP",
    timeout_seconds: int = 120,
) -> bool:
    """
    Run the blocking consent dialog in a thread executor so it does not
    block the async event loop. Auto-denies and returns False if the user
    does not respond within timeout_seconds.
    """
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                None,
                consent.request_consent,
                site_name,
                target_url,
                inject_as,
                agent_description,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.warning("consent timed out after %ss — auto-denied", timeout_seconds)
        return False
    
    
def _filter_response(data: object, fields: list[str] | None) -> object:
    """
    Trim a JSON-serialisable value to only the requested top-level keys
    before it is returned to the model context.
 
    Handles three shapes:
      - dict           → keep only keys in `fields`
      - list of dicts  → apply the same filter to every item
      - anything else  → returned unchanged (fields has no effect)

    Args:
        data:   The raw response value from proxy_request / api_client.
        fields: Key names to keep. None or empty list → no filtering.
 
    Returns:
        Filtered value, or the original value if filtering doesn't apply.
    """
    if not fields:
        return data
    if isinstance(data, dict):
        return {k: data[k] for k in fields if k in data}
    if isinstance(data, list):
        return [
            {k: item[k] for k in fields if k in item}
            if isinstance(item, dict) else item
            for item in data
        ]
    return data


# ── Tool functions ────────────────────────────────────────────────────────────

async def list_vault_sites() -> dict:
    """
    List all sites stored in the vault.

    Returns site names and username hints only — never passwords or VEK.
    Agents use this to discover what credentials are available.

    Returns:
        {"sites": [{"site_name": "...", "username_hint": "..."}, ...], "total": N}
    """
    if not is_logged_in():
        return {
            "error": "Not logged in. Run 'psamvault login' in your terminal first"
        }

    try:
        access_token = get_access_token()
        entries = await api_client.list_vault_entries(access_token)
        return {
            "sites": [
                {
                    "site_name": e["site_name"],
                    "username_hint": e.get("username_hint"),
                }
                for e in entries
            ],
            "total": len(entries),
        }
    except Exception as e:
        return {"error": str(e)}


async def check_credential_exists(site_name: str) -> dict:
    """
    Check whether a credential is stored for a given site.
 
    Returns the username hint if available — never the password or VEK.
    Agents use this before use_credential to avoid unnecessary consent
    prompts for credentials that don't exist.
 
    Args:
        site_name: The site to check, e.g. "github.com".
 
    Returns:
        {"exists": bool, "site_name": str, "username_hint": str|None}
    """
    if not is_logged_in():
        return {
            "error": "Not logged in. Run  psamvault login  in your terminal first."
        }

    try:
        access_token = get_access_token()
        return await api_client.check_site_exists(access_token, site_name)
    except Exception as e:
        return  {"error": str(e)}
    

async def use_credential(
    site_name: str,
    target_url: str,
    method: str = "GET",
    inject_as: str = "bearer_token",
    header_name: str | None = None,
    body: dict | None = None,
    extra_headers: dict | None = None,
    fields: list[str] | None = None,
) -> dict:
    """
    Make an authenticated HTTP request using a stored credential.
 
    Flow:
      1. Show user a consent prompt — blocked without approval
      2. Read VEK from the OS keychain via session.get_vek()
      3. Fetch encrypted blob from psamvault API
      4. Decrypt locally with VEK using AES-256-GCM
      5. Pass plaintext credential to backend proxy over HTTPS
      6. Backend injects credential into outbound request
      7. Filter response to `fields` if provided             
      8. Return (filtered) HTTP response — credential never appears
 
    Args:
        site_name:     Vault site to use, e.g. "github.com".
        target_url:    URL to call, e.g. "https://api.github.com/user".
        method:        HTTP method — GET, POST, PUT, PATCH, DELETE.
        inject_as:     Injection mode:
                         bearer_token   → Authorization: Bearer <password>
                         api_key_header → <header_name>: <password>
                         basic_auth     → Authorization: Basic base64(user:pass)
        header_name:   Required when inject_as="api_key_header".
        body:          Optional JSON body for POST/PUT/PATCH.
        extra_headers: Optional additional request headers.
        fields:        Optional list of top-level JSON keys to keep in the
                       response body before it is returned to the model.
                       Works on both dict and list-of-dict response bodies.
                       Example: ["login", "id"] trims a GitHub user object
                       from ~40 fields down to 2, saving ~250 tokens.
 
    Returns:
        {
            "status_code":   int,
            "response_body": str | dict | list,  # filtered if `fields` given
            "site_name":     str,
            "injected_as":   str,
            "target_url":    str,
            "fields_applied": list[str] | None,  # echoed back if filtering happened
        }
        or {"error": str} if denied or something fails.

    """
    if not is_logged_in():
        return {
            "error": "Not logged in. Run  psamvault login  in your terminal first."
        }

    # Reject non-http(s) schemes before touching the credential or showing
    # the consent dialog — defense-in-depth alongside the backend validator.
    _parsed_target = urlparse(target_url)
    if _parsed_target.scheme not in ("http", "https"):
        return {
            "error": (
                f"target_url scheme '{_parsed_target.scheme}' is not allowed. "
                "Only http and https are permitted."
            )
        }

    # User consent - mandatory gate before any credential is accessed
    try:
        approved = await _request_consent_async(
            site_name=site_name,
            target_url=target_url,
            inject_as=inject_as,
        )
    except ConsentGUIUnavailableError as e:
        return {"error": str(e)}
    
    if not approved:
        return {
            "error": (
                f"Access denied by user. "
                f"Credential for '{site_name}' was not used"
            )
        }
        
    
    # Decrypt credential locally using VEK from session
    # Never logged, never returned to agent
    try:
        credentials = await _decrypt_site_credential(site_name)
    except RuntimeError as e:
        return {"error": str(e)}
    except Exception as e:
        return {
           "error": (
                f"Could not decrypt credential for '{site_name}'. "
                f"Your session may be stale — try  psamvault login  again. "
                f"Detail: {str(e)}"
            ) 
        }
    
    
    try:
        access_token = get_access_token()
        result = await api_client.proxy_request(
            access_token=access_token,
            site_name=site_name,
            target_url=target_url,
            method=method,
            inject_as=inject_as,
            header_name=header_name,
            body=body,
            extra_headers=extra_headers,
            credential_username=credentials["username"],
            credential_password=credentials["password"],
        )
    except Exception as e:
        return {"error": f"Proxy request failed: {str(e)}"}

    try:
        consent.notify_completion(
            site_name=site_name,
            status_code=result["status_code"],
            target_url=target_url
        )
    except Exception as e:
        logger.warning("notification failed (non-fatal): %s", e)

    # ── TOKEN OPTIMISATION: filter response_body before it hits the model ──
    # proxy_request returns {"status_code", "response_body", "site_name",
    # "injected_as", "target_url"}. response_body may be a parsed dict/list
    # or a raw string depending on Content-Type. We filter only when it is
    # a dict or list and `fields` was provided.
    if fields and "response_body" in result:
        raw_body = result["response_body"]
        # If response_body is a JSON string, parse it first so we can filter
        if isinstance(raw_body, str):
            try:
                raw_body = json.loads(raw_body)
            except (ValueError, TypeError):
                pass  # Not JSON — leave as-is, fields has no effect

        filtered_body = _filter_response(raw_body, fields)
        result = {**result, "response_body": filtered_body, "fields_applied": fields}
    else:
        result = {**result, "fields_applied": None}

    return result
    

async def get_username_for_site(site_name: str) -> dict:
    """
    Return the username (not password) stored for a site.
 
    Useful when an agent needs the username for a form field or request
    body without needing authentication. Requires user consent.
    The VEK is used to decrypt locally — the password field is discarded
    before the result is returned to the agent.
 
    Args:
        site_name: The site to get the username for.
 
    Returns:
        {"site_name": str, "username": str} or {"error": str}
    """
    if not is_logged_in():
        return {
            "error": "Not logged in. Run  psamvault login  in your terminal first."
        }
        
    try:
        approved = await _request_consent_async(
            site_name=site_name,
            target_url="(username only — no HTTP request will be made)",
            inject_as="username_only",
        )
    except ConsentGUIUnavailableError as e:
        return {"error": str(e)}
    
    if not approved:
        return {"error": "Access denied by user"}
    
    try:
        credentials = await _decrypt_site_credential(site_name)
        return {
            "site_name": site_name,
            "username": credentials["username"]
        }
    except RuntimeError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


async def browser_login(
    site_name: str,
    login_url: str | None = None,
    username_selector: str | None = None,
    password_selector: str | None = None,
    submit_selector: str | None = None,
    timeout_ms: int = 8000,
) -> dict:
    """
    Open a headed browser and securely log into a site using a stored credential.
 
    Flow:
      1. Open a real Chromium browser (visible to the user).
      2. Navigate to the site homepage (or login_url if provided).
      3. Auto-discover the login page by looking for a sign-in link and
         clicking it — NO credential access happens here.
         Exception: if a saved session was loaded and no sign-in link is
         found, the user is already authenticated — return success immediately
         (step "session_reused_already_logged_in") without accessing any
         credential or showing a consent dialog.
      4. Take a screenshot of the confirmed login page so the user can see
         exactly where their credential will be used.
      5. Show the user a consent dialog with the confirmed login URL.
      6. Only after approval: decrypt the credential and fill the form.
      7. Handle multi-step flows (e.g., "Continue with Email" → email → Next →
         password → submit) using semantic locators.
      8. Persist the browser session on success for future reuse.

    Uses semantic Playwright locators (get_by_role, get_by_label) that pierce
    Shadow DOM, React, and Vue apps. Falls back to CSS selectors when needed.

    All parameters except site_name are optional.

    Args:
        site_name:         Vault site whose credential to use, e.g. "github.com".
        login_url:         Full URL of the login page (optional — auto-discovered if omitted).
        username_selector: CSS selector for the username/email field (optional).
        password_selector: CSS selector for the password field (optional).
        submit_selector:   CSS selector for the final submit button (optional).
        timeout_ms:        Per-step detection timeout in milliseconds (default 8000).

    Returns:
        A concise summary dict — NOT the raw Playwright step list:
        {
            "success":               bool,
            "steps_count":           int    — how many steps completed before returning,
            "failed_at":             str | None — step name where flow stopped (None = success),
            "url":                   str | None — final page URL,
            "title":                 str | None — final page title,
            "error_text":            str | None — visible error message detected on page,
            "hint":                  str | None — actionable suggestion when a step fails,
            "login_page_screenshot": str | None — path to screenshot of the login page,
        }
        
    TOKEN NOTE:
        The old response included steps_completed as a full list
        (e.g. ["navigated_to_site", "found_and_clicked_login_link", ...]).
        Each step string costs ~5 tokens; a typical login has 8–12 steps = ~60 tokens.
        steps_count (int) replaces this list, cutting that to 1 token.
        failed_at and hint are preserved so the agent can retry intelligently.
    """
    STORAGE_DIR = Path.home() / ".psamvault" / "browser_sessions"
    _safe_name = re.sub(r'[<>:"/\\|?*\s]', '_', site_name)
    storage_path = STORAGE_DIR / f"{_safe_name}.json"

     # Internal step tracker — kept as a list for logic/branching inside this
    # function, but only the count is returned to the model.
    _steps: list[str] = []
    failed_at: str | None = None
    screenshot_path: "Path | None" = None
    captcha_screenshot_path: "Path | None" = None
    captcha_detected: bool = False
    _login_url_discovered: bool = False

    def _result(*, success: bool, url=None, title=None, error_text=None, hint=None) -> dict:
        """
        Build the return dict.
 
        TOKEN OPTIMISATION: expose steps_count (int) instead of the full
        steps_completed list. The agent only needs to know whether things
        progressed; it doesn't need to read every step name.
        failed_at and hint give it enough context to retry on failure.
        """
        if success:
            message = (
                f"Successfully logged into {site_name}. "
                "Please inform the user that the login was successful."
            )
        else:
            message = None
        return {
            "success":               success,
            "message":               message,
            "captcha_detected":      captcha_detected,
            "steps_count":           len(_steps),
            "failed_at":             failed_at,
            "url":                   url,
            "title":                 title,
            "error_text":            error_text,
            "hint":                  hint,
            "login_page_screenshot": str(screenshot_path) if screenshot_path else None,
            "captcha_screenshot":    str(captcha_screenshot_path) if captcha_screenshot_path else None,
        }

    if not is_logged_in():
        return {"error": "Not logged in. Run  psamvault login  in your terminal first."}

    # Reject non-http(s) login_url schemes before opening the browser.
    # Without this, a malicious agent could supply file:// or javascript:
    # URLs, causing Playwright to render local files and capture them in
    # the login page screenshot.
    if login_url:
        _parsed_login = urlparse(login_url)
        if _parsed_login.scheme not in ("http", "https"):
            return {
                "error": (
                    f"login_url scheme '{_parsed_login.scheme}' is not allowed. "
                    "Only http and https are permitted."
                )
            }

    # Protect the MCP stdio transport: Chromium can write to stdout on startup,
    # which corrupts the JSON-RPC stream. redirect_stdout ensures restoration
    # on every exit path (normal return, early return, or exception).
    _stdout_redirect = contextlib.redirect_stdout(sys.stderr)
    _stdout_redirect.__enter__()
    try:
        async with async_playwright() as p:
            context_kwargs: dict = {}
            if storage_path.exists():
                context_kwargs["storage_state"] = str(storage_path)

            browser = await p.chromium.launch(
                headless=False,
                args=[
                    "--disable-logging",
                    "--log-level=3",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )
            _ACTIVE_BROWSERS.add(browser)
            context = await browser.new_context(**context_kwargs)
            page = await context.new_page()

            # ── Semantic field finder ─────────────────────────────────────
            async def _find_field(
                semantic_patterns: list[str],
                css_fallbacks: list[str],
                t_ms: int = timeout_ms,
            ):
                loop = asyncio.get_running_loop()
                deadline = loop.time() + t_ms / 1000
                while loop.time() < deadline:
                    for pattern in semantic_patterns:
                        for loc in [
                            page.get_by_role("textbox", name=re.compile(pattern, re.I)),
                            page.get_by_label(re.compile(pattern, re.I)),
                        ]:
                            try:
                                if await loc.first.is_visible(timeout=500):
                                    return loc.first
                            except Exception:
                                pass
                    for sel in css_fallbacks:
                        try:
                            loc = page.locator(sel).first
                            if await loc.is_visible(timeout=500):
                                return loc
                        except Exception:
                            pass
                    await asyncio.sleep(0.25)
                return None

            # ── Semantic button/link finder ───────────────────────────────
            async def _find_button(
                text_patterns: list[str],
                css_fallbacks: list[str],
                t_ms: int = 3000,
            ):
                loop = asyncio.get_running_loop()
                deadline = loop.time() + t_ms / 1000
                while loop.time() < deadline:
                    for pattern in text_patterns:
                        for role in ("button", "link"):
                            try:
                                loc = page.get_by_role(role, name=re.compile(pattern, re.I))
                                if await loc.first.is_visible(timeout=500):
                                    return loc.first
                            except Exception:
                                pass
                    for sel in css_fallbacks:
                        try:
                            loc = page.locator(sel).first
                            if await loc.is_visible(timeout=500):
                                return loc
                        except Exception:
                            pass
                    await asyncio.sleep(0.25)
                return None
            # ─────────────────────────────────────────────────────────────

            # ── Phase 0: Navigate ─────────────────────────────────────────
            target = login_url or f"https://{site_name}"
            await page.goto(target, wait_until="domcontentloaded")
            _steps.append("navigated_to_site")

            if not login_url:
                sign_in_btn = await _find_button(
                    text_patterns=[r"sign[\s\-]?in", r"log[\s\-]?in", r"^login$"],
                    css_fallbacks=["[href*='login' i]", "[href*='signin' i]", "[href*='sign-in' i]"],
                    t_ms=5000,
                )
                if sign_in_btn:
                    await sign_in_btn.click()
                    await page.wait_for_load_state("domcontentloaded")
                    _steps.append("found_and_clicked_login_link")
                    login_url = page.url
                    _login_url_discovered = True
                else:
                    if storage_path.exists():
                        _steps.append("session_reused_already_logged_in")
                        current_url = page.url
                        current_title = await page.title()
                        try:
                            await page.wait_for_event("close", timeout=600_000)
                        except BaseException:
                            pass
                        return _result(success=True, url=current_url, title=current_title)
                    failed_at = "login_link_not_found"
                    return _result(
                        success=False,
                        url=page.url,
                        title=await page.title(),
                        hint=(
                            f"Could not find a sign-in link on https://{site_name}. "
                            "Provide login_url explicitly."
                        ),
                    )
            # ─────────────────────────────────────────────────────────────

            # ── Phase 0b: Screenshot ──────────────────────────────────────
            # Capture the confirmed login page so the user can see exactly
            # where their credential will be used before approving.
            _screenshot_file = STORAGE_DIR / f"{_safe_name}_login_preview.png"
            try:
                STORAGE_DIR.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(_screenshot_file), full_page=False)
                screenshot_path = _screenshot_file
                _steps.append("login_page_screenshot_taken")
            except Exception:
                pass  # Non-fatal


            # ── Phase 0c: Consent ─────────────────────────────────────────
            # Request consent NOW with the confirmed login URL so the user
            # knows exactly which page their credential will be filled on.
            try:
                approved = await _request_consent_async(
                    site_name=site_name,
                    target_url=login_url,
                    inject_as="browser_form_fill",
                )
            except ConsentGUIUnavailableError as e:
                return {"error": str(e)}

            if not approved:
                return {"error": f"Access denied by user. Credential for '{site_name}' was not used."}
            # ─────────────────────────────────────────────────────────────

            # ── Persist auto-discovered login URL ─────────────────────────
            # If the login URL was not provided and we found it via semantic
            # discovery, save it back to the vault so future calls skip the
            # sign-in link search and go directly to the login page.
            if _login_url_discovered:
                try:
                    access_token = get_access_token()
                    await api_client.update_vault_entry_url(
                        access_token=access_token,
                        site_name=site_name,
                        login_url=login_url,
                    )
                    _steps.append("login_url_persisted")
                except Exception:
                    pass  # Non-fatal — the login still works next time via session reuse
            # ─────────────────────────────────────────────────────────────

            # ── Phase 0d: Decrypt credential ──────────────────────────────
            # Decrypt only after consent — credential is held in memory for
            # the duration of the form-fill steps below, then discarded.
            try:
                credentials = await _decrypt_site_credential(site_name)
            except RuntimeError as e:
                return {"error": str(e)}
            except Exception as e:
                return {
                    "error": (
                        f"Could not decrypt credential for '{site_name}'. "
                        f"Your session may be stale — try  psamvault login  again. "
                        f"Detail: {str(e)}"
                    )
                }
            # ─────────────────────────────────────────────────────────────
            if username_selector:
                username_field = page.locator(username_selector).first
                try:
                    await username_field.wait_for(state="visible", timeout=timeout_ms)
                except Exception:
                    failed_at = "username_selector_not_found"
                    return _result(
                        success=False,
                        url=page.url,
                        title=await page.title(),
                        hint=f"Provided username_selector '{username_selector}' was not visible.",
                    )
            else:
                username_field = await _find_field(
                    semantic_patterns=["email", "username", "user", "login"],
                    css_fallbacks=["input[type='email']", "input[type='text']"],
                    t_ms=2000,
                )
                if not username_field:
                    gateway = await _find_button(
                        text_patterns=[
                            r"continue.?with.?email", r"sign.?in.?with.?email",
                            r"use.?email", r"^email$",
                        ],
                        css_fallbacks=["[data-provider='email']"],
                        t_ms=4000,
                    )
                    if gateway:
                        await gateway.click()
                        _steps.append("clicked_gateway_button")
                        username_field = await _find_field(
                            semantic_patterns=["email", "username", "user"],
                            css_fallbacks=["input[type='email']", "input[type='text']"],
                            t_ms=6000,
                        )

            if not username_field:
                failed_at = "username_field_not_found"
                return _result(
                    success=False,
                    url=page.url,
                    title=await page.title(),
                    hint=(
                        "Could not detect the username/email field. "
                        "The page may use Shadow DOM or a non-standard flow. "
                        "Provide username_selector explicitly."
                    ),
                )

            await username_field.click()
            await username_field.fill(credentials["username"])
            await username_field.press("Tab")
            await page.wait_for_load_state("domcontentloaded")
            _steps.append("filled_username")
            # ─────────────────────────────────────────────────────────────

            # ── Phase 2: Locate and fill the password field ───────────────
            if password_selector:
                password_field = page.locator(password_selector).first
                try:
                    await password_field.wait_for(state="visible", timeout=timeout_ms)
                except Exception:
                    failed_at = "password_selector_not_found"
                    return _result(
                        success=False,
                        url=page.url,
                        title=await page.title(),
                        hint=f"Provided password_selector '{password_selector}' was not visible.",
                    )
            else:
                password_field = await _find_field(
                    semantic_patterns=["password", "pass"],
                    css_fallbacks=["input[type='password']"],
                    t_ms=1500,
                )
                if not password_field:
                    # Multi-step: click Next/Continue to reveal the password field
                    next_btn = await _find_button(
                        text_patterns=[r"^next$", r"^continue$"],
                        css_fallbacks=["button[type='submit']", "input[type='submit']"],
                        t_ms=3000,
                    )
                    if next_btn:
                        await next_btn.click()
                        _steps.append("clicked_next")
                        password_field = await _find_field(
                            semantic_patterns=["password", "pass"],
                            css_fallbacks=["input[type='password']"],
                            t_ms=6000,
                        )

            if not password_field:
                failed_at = "password_field_not_found"
                return _result(
                    success=False,
                    url=page.url,
                    title=await page.title(),
                    hint=(
                        "Could not detect the password field. "
                        "The site may use OTP/magic-link or SSO. "
                        "Provide password_selector explicitly."
                    ),
                )

            await password_field.click()
            await password_field.fill(credentials["password"])
            await password_field.press("Tab")
            await page.wait_for_load_state("domcontentloaded")
            _steps.append("filled_password")
            # ─────────────────────────────────────────────────────────────

            # ── Phase 3: Submit / CAPTCHA handoff ─────────────────────────
            if submit_selector:
                submit_btn = page.locator(submit_selector).first
                try:
                    await submit_btn.wait_for(state="visible", timeout=timeout_ms)
                except Exception:
                    failed_at = "submit_selector_not_found"
                    return _result(
                        success=False,
                        url=page.url,
                        title=await page.title(),
                        hint=f"Provided submit_selector '{submit_selector}' was not visible.",
                    )
            else:
                submit_btn = await _find_button(
                    text_patterns=[
                        r"sign[\s\-]?in", r"log[\s\-]?in", r"^login$",
                        r"^continue$", r"^submit$",
                    ],
                    css_fallbacks=["button[type='submit']", "input[type='submit']"],
                    t_ms=3000,
                )
                if not submit_btn:
                    failed_at = "submit_button_not_found"
                    return _result(
                        success=False,
                        url=page.url,
                        title=await page.title(),
                        hint="Could not detect submit button. Provide submit_selector explicitly.",
                    )

            _CAPTCHA_SELECTORS = [
                "iframe[src*='recaptcha']",
                "iframe[src*='hcaptcha']",
                "iframe[src*='turnstile']",
                ".g-recaptcha",
                "#hcaptcha",
                "[class*='captcha' i]",
                "[id*='captcha' i]",
            ]

            async def _has_visible_captcha(t_ms: int = 1000) -> bool:
                for _sel in _CAPTCHA_SELECTORS:
                    try:
                        if await page.locator(_sel).first.is_visible(timeout=t_ms):
                            return True
                    except Exception:
                        pass
                return False

            async def _handle_captcha_handoff(stage: str) -> dict:
                """
                Immediately stop automation when a CAPTCHA is detected.

                Takes a screenshot, prints a message to stderr, then returns
                a result instructing the agent to tell the user to solve the
                CAPTCHA and use the CLI to retrieve the password manually.

                Args:
                    stage: Label for the step log — 'before_submit' or 'after_submit'.

                Returns:
                    A result dict the caller should return immediately.
                """
                nonlocal captcha_detected, failed_at, captcha_screenshot_path

                captcha_detected = True
                failed_at = "captcha_user_action_required"
                _steps.append(f"captcha_handoff_{stage}")

                try:
                    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
                    _cap_file = STORAGE_DIR / f"{_safe_name}_captcha.png"
                    await page.screenshot(path=str(_cap_file), full_page=False)
                    captcha_screenshot_path = _cap_file
                    _steps.append("captcha_screenshot_taken")
                except Exception:
                    pass

                logger.info(
                    "CAPTCHA detected on %s — automation stopped", site_name,
                )

                return _result(
                    success=False,
                    url=page.url,
                    title=await page.title(),
                    hint=(
                        "CAPTCHA detected — automation cannot proceed. "
                        "Tell the user to either solve the CAPTCHA and log in manually, "
                        "or use the CLI command 'psamvault get <site>' to retrieve "
                        "the credential and sign in themselves."
                    ),
                )

            # CAPTCHA may appear in two places:
            #   1. Before submit — already visible after filling credentials.
            #   2. After submit  — some sites reveal it only after the first click.
            if await _has_visible_captcha():
                return await _handle_captcha_handoff("before_submit")
            else:
                await submit_btn.click()
                _steps.append("submitted_form")

                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass

                if await _has_visible_captcha():
                    return await _handle_captcha_handoff("after_submit")

            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            # ─────────────────────────────────────────────────────────────

            final_url = page.url
            final_title = await page.title()

            # ── Detect visible error message on resulting page ────────────
            error_text = None
            for sel in [
                "[role='alert']", ".error", ".flash-error", "#error",
                ".alert-danger", ".alert-error",
                "[class*='error' i]", "[class*='invalid' i]",
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible():
                        error_text = (await el.inner_text()).strip()
                        break
                except Exception:
                    continue
            # ─────────────────────────────────────────────────────────────

            # ── Determine success ─────────────────────────────────────────
            # Login is considered successful only when:
            #   (a) no visible error element is found on the result page, AND
            #   (b) the browser navigated away from the login URL after submit.
            # A page stuck on the same URL usually means credentials were
            # rejected silently with no explicit error element shown.
            url_changed = final_url != login_url
            login_succeeded = error_text is None and url_changed
            # ─────────────────────────────────────────────────────────────

            # ── Persist session state on success ─────────────────────────
            if login_succeeded:
                STORAGE_DIR.mkdir(parents=True, exist_ok=True)
                await context.storage_state(path=str(storage_path))
                _steps.append("session_state_saved")
            # ─────────────────────────────────────────────────────────────

            # Keep browser open for CAPTCHA / 2FA / continued session use.
            try:
                await page.wait_for_event("close", timeout=600_000)
            except BaseException:
                pass

            return _result(
                success=login_succeeded,
                url=final_url,
                title=final_title,
                error_text=error_text,
                hint=(
                    "Login may have failed — the page did not navigate away from "
                    "the login URL after submission. Try providing login_url or "
                    "username_selector/password_selector explicitly."
                ) if not url_changed and error_text is None else None,
            )

    except Exception as e:
        failed_at = failed_at or "unexpected_error"
        return _result(
            success=False,
            error_text=str(e),
            hint="An unexpected error occurred. Check the terminal for details.",
        )
    finally:
        _stdout_redirect.__exit__(None, None, None)