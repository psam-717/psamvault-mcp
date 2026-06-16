"""
Browser login flow for psamvault MCP.

This module contains the login flow logic that was originally in
the CLI's browser_commands.py. It's extracted here so both the
CLI one-shot mode and the MCP server can share the same flow,
while the MCP server manages the Playwright browser directly
(in-process) instead of via a subprocess daemon.

Architecture:
  The MCP server calls run_login_flow() with a Playwright Browser
  instance that it manages as a singleton. No subprocess, no
  stdin/stdout daemon protocol — just a direct async function call.
"""

import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from mcp_server.log import get_logger

logger = get_logger()

_CAPTCHA_SELECTORS = [
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "iframe[src*='turnstile']",
    ".g-recaptcha",
    "#hcaptcha",
]

STORAGE_DIR = Path.home() / ".psamvault" / "browser_sessions"


# ── Login page helpers ────────────────────────────────────────────────────────


def _discover_login_url(page) -> str | None:
    """Find and click a sign-in/log-in link, return the resulting URL."""
    text_patterns = [
        r"sign[\s\-]?in", r"log[\s\-]?in", r"^login$",
    ]
    css_fallbacks = [
        "[href*='login' i]", "[href*='signin' i]",
        "[href*='sign-in' i]", "[href*='log-in' i]",
    ]
    for pattern in text_patterns:
        for role in ("link", "button"):
            try:
                loc = page.get_by_role(role, name=re.compile(pattern, re.I))
                if loc.first.is_visible(timeout=1000):
                    loc.first.click()
                    page.wait_for_load_state("domcontentloaded", timeout=15_000)
                    return page.url
            except Exception:
                pass
    for sel in css_fallbacks:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=500):
                loc.click()
                page.wait_for_load_state("domcontentloaded", timeout=15_000)
                return page.url
        except Exception:
            pass
    return None


def _fill_field(locator, value: str) -> None:
    """Fill a form field. Falls back to type() if fill() doesn't register."""
    locator.click()
    locator.fill(value)
    try:
        if locator.input_value() != value:
            locator.clear()
            locator.type(value, delay=40)
    except Exception:
        pass


def _url_origin_path(url: str) -> str:
    """Return scheme+host+path (no trailing slash, query, or fragment).
    
    Normalises `www.` prefix so that `https://www.kaggle.com` and
    `https://kaggle.com` compare as equal.
    """
    p = urlparse(url)
    netloc = p.netloc
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return f"{p.scheme}://{netloc}{p.path.rstrip('/')}"


def _has_visible_captcha(page, t_ms: int = 1000) -> bool:
    """Check if a CAPTCHA iframe or widget is visible on the page."""
    for _sel in _CAPTCHA_SELECTORS:
        try:
            if page.locator(_sel).first.is_visible(timeout=t_ms):
                return True
        except Exception:
            pass
    return False


def _poll_for_username(page, t_ms: int):
    semantic_patterns = ["email", "username", "user name", "login"]
    css_fallbacks = [
        'input[type="email"]', 'input[name="email"]', 'input[id="email"]',
        'input[name="username"]', 'input[id="username"]',
        'input[autocomplete="username"]', 'input[autocomplete="email"]',
        'input[type="text"]',
    ]
    deadline = time.monotonic() + t_ms / 1000
    while time.monotonic() < deadline:
        for pattern in semantic_patterns:
            for locator in [
                page.get_by_role("textbox", name=re.compile(pattern, re.I)),
                page.get_by_label(re.compile(pattern, re.I)),
            ]:
                try:
                    if locator.first.is_visible(timeout=500):
                        return locator.first
                except Exception:
                    pass
        for sel in css_fallbacks:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=500):
                    return loc
            except Exception:
                pass
        time.sleep(0.25)
    return None


def _find_gateway_button(page):
    """Find a 'Continue with email' / 'Sign in with email' button."""
    text_patterns = [
        r"continue.?with.?email", r"sign.?in.?with.?email",
        r"use.?email", r"^email$",
    ]
    for pattern in text_patterns:
        for role in ("button", "link"):
            try:
                loc = page.get_by_role(role, name=re.compile(pattern, re.I))
                if loc.first.is_visible(timeout=500):
                    return loc.first
            except Exception:
                pass
    try:
        loc = page.locator("[data-provider='email']").first
        if loc.is_visible(timeout=500):
            return loc
    except Exception:
        pass
    return None


def _find_username_field(page, timeout_ms: int = 8000):
    """Locate the username/email input, handling multi-step flows."""
    field = _poll_for_username(page, t_ms=min(4000, timeout_ms))
    if field:
        return field
    gateway = _find_gateway_button(page)
    if gateway:
        gateway.click()
        field = _poll_for_username(page, t_ms=min(6000, timeout_ms))
    return field


def _poll_for_password(page, t_ms: int):
    semantic_patterns = ["password", "pass"]
    css_fallbacks = [
        'input[type="password"]', 'input[name="password"]', 'input[id="password"]',
        'input[autocomplete="current-password"]',
    ]
    deadline = time.monotonic() + t_ms / 1000
    while time.monotonic() < deadline:
        for pattern in semantic_patterns:
            for locator in [
                page.get_by_role("textbox", name=re.compile(pattern, re.I)),
                page.get_by_label(re.compile(pattern, re.I)),
            ]:
                try:
                    if locator.first.is_visible(timeout=500):
                        return locator.first
                except Exception:
                    pass
        for sel in css_fallbacks:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=500):
                    return loc
            except Exception:
                pass
        time.sleep(0.25)
    return None


def _find_next_button(page):
    """Find a Next/Continue button that reveals the password field."""
    text_patterns = [r"^next$", r"^continue$"]
    for pattern in text_patterns:
        for role in ("button", "link"):
            try:
                loc = page.get_by_role(role, name=re.compile(pattern, re.I))
                if loc.first.is_visible(timeout=500):
                    return loc.first
            except Exception:
                pass
    for sel in ("button[type='submit']", "input[type='submit']"):
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=500):
                return loc
        except Exception:
            pass
    return None


def _find_password_field(page, timeout_ms: int = 8000):
    """Locate the password input, handling multi-step flows."""
    field = _poll_for_password(page, t_ms=min(2000, timeout_ms))
    if field:
        return field
    next_btn = _find_next_button(page)
    if next_btn:
        next_btn.click()
        field = _poll_for_password(page, t_ms=min(6000, timeout_ms))
    return field


def _submit_form(page, timeout_ms: int = 8000):
    """Find and return the form submit button."""
    text_patterns = [
        r"sign.?in", r"log.?in", r"^login$", r"^continue$", r"^submit$",
    ]
    for pattern in text_patterns:
        for role in ("button", "link"):
            try:
                loc = page.get_by_role(role, name=re.compile(pattern, re.I))
                if loc.first.is_visible(timeout=500):
                    return loc.first
            except Exception:
                pass
    for sel in ('button[type="submit"]', 'input[type="submit"]'):
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=500):
                return loc
        except Exception:
            pass
    return None


# ── Result builder ────────────────────────────────────────────────────────────


def _build_result(
    *,
    success: bool,
    site: str,
    steps: list[str],
    captcha_detected: bool = False,
    failed_at: str | None = None,
    url: str | None = None,
    title: str | None = None,
    error_text: str | None = None,
    hint: str | None = None,
    login_page_screenshot_path: str | None = None,
    captcha_screenshot_path: str | None = None,
) -> dict:
    """Build the standardised result dict for the login flow."""
    return {
        "success": success,
        "message": f"Successfully logged into {site}." if success else None,
        "captcha_detected": captcha_detected,
        "steps_count": len(steps),
        "failed_at": failed_at,
        "url": url,
        "title": title,
        "error_text": error_text,
        "hint": hint,
        "login_page_screenshot": login_page_screenshot_path,
        "captcha_screenshot": captcha_screenshot_path,
    }


# ── Core login flow ───────────────────────────────────────────────────────────


async def run_login_flow(
    browser,
    site_name: str,
    credentials: dict,
    login_url: str | None = None,
    username_selector: str | None = None,
    password_selector: str | None = None,
    submit_selector: str | None = None,
    timeout_ms: int = 8000,
) -> dict:
    """Run the full login flow using the given Playwright Browser.

    This is the same logic used by the CLI's psamvault open command,
    but as an async function that takes an already-launched Browser.

    The login flow returns as soon as login success or failure is
    determined (URL change, form fields disappeared, CAPTCHA, etc.).
    The browser context stays open so the user can continue using
    the browser window after a successful login.

    If the user closes the browser tab, Playwright handles the
    disconnect silently on the next browser_login call via the
    singleton browser manager's auto-recovery.

    Args:
        browser: A playwright.async_api.Browser instance.
        site_name: The site to log into.
        credentials: {"username": ..., "password": ..., "notes": ...}
        login_url: Optional explicit login page URL.
        username_selector: Optional explicit CSS selector for the username field.
        password_selector: Optional explicit CSS selector for the password field.
        submit_selector: Optional explicit CSS selector for the submit button.
        timeout_ms: Per-step detection timeout in milliseconds.

    Returns:
        A result dict with success, message, captcha_detected, etc.
    """
    _safe_name = re.sub(r'[<>:"/\\|?*\s]', '_', site_name)
    storage_path = STORAGE_DIR / f"{_safe_name}.json"

    _steps: list[str] = []
    captcha_detected = False
    captcha_screenshot_path: str | None = None
    login_page_screenshot_path: str | None = None
    failed_at: str | None = None
    login_succeeded = False

    def _r(*, success: bool, url=None, title=None, error_text=None, hint=None) -> dict:
        return _build_result(
            success=success, site=site_name, steps=_steps,
            captcha_detected=captcha_detected, failed_at=failed_at,
            url=url, title=title, error_text=error_text, hint=hint,
            login_page_screenshot_path=login_page_screenshot_path,
            captcha_screenshot_path=captcha_screenshot_path,
        )

    base_url = f"https://{site_name}"
    target = login_url or base_url

    # ── Load saved session state if available ───────────
    context_kwargs = {}
    if storage_path.exists():
        context_kwargs["storage_state"] = str(storage_path)
        _steps.append("loaded_saved_session")

    context = await browser.new_context(**context_kwargs)
    page = await context.new_page()
    _steps.append("browser_launched")

    try:
        await page.goto(target, wait_until="domcontentloaded", timeout=30_000)
        _steps.append("navigated_to_site")

        # ── Login URL discovery ───────────────────────────────────────────
        if not login_url:
            current_origin = _url_origin_path(page.url)
            base_origin = _url_origin_path(base_url)
            if current_origin != base_origin:
                # URL changed from the base — either a redirect (www.kaggle.com
                # vs kaggle.com) or we landed on a login page by session reuse.
                # If it still looks like the same domain (just with www. prefix
                # already handled by _url_origin_path) AND we have a saved
                # session, check whether this is truly a login page.
                if storage_path.exists():
                    # Peek: does this page actually have login form elements?
                    login_form_found = _find_username_field(page, timeout_ms=2000)
                    if login_form_found is None:
                        # No username field visible — we're already logged in
                        _steps.append("session_reused_already_logged_in")
                        return _r(success=True, url=page.url, title=await page.title())
                login_url = page.url
            else:
                discovered = _discover_login_url(page)
                if discovered and _url_origin_path(discovered) != base_origin:
                    login_url = discovered
                else:
                    if storage_path.exists():
                        # Verify: if the page has no login form fields,
                        # the saved session is actually working and
                        # we're already logged in.
                        login_form_found = _find_username_field(page, timeout_ms=2000)
                        if login_form_found is None:
                            _steps.append("session_reused_already_logged_in")
                            return _r(success=True, url=page.url, title=await page.title())
                        # Login form IS visible — session didn't work.
                        # Fall through and treat page.url as the login page.
                        login_url = page.url
                    else:
                        failed_at = "login_link_not_found"
                        return _r(
                            success=False, url=page.url,
                            hint=f"Could not find a sign-in link on {base_url}. "
                                 "Provide login_url explicitly.",
                        )

        # ── Login page screenshot ─────────────────────────────────────────
        try:
            STORAGE_DIR.mkdir(parents=True, exist_ok=True)
            _screenshot_file = STORAGE_DIR / f"{_safe_name}_login_preview.png"
            await page.screenshot(path=str(_screenshot_file), full_page=False)
            login_page_screenshot_path = str(_screenshot_file)
            _steps.append("login_page_screenshot_taken")
        except Exception:
            pass

        # ── Username field ─────────────────────────────────────────────────
        if username_selector:
            username_field = page.locator(username_selector).first
            try:
                await username_field.wait_for(state="visible", timeout=timeout_ms)
            except Exception:
                failed_at = "username_selector_not_found"
                return _r(
                    success=False, url=page.url,
                    hint=f"Provided username_selector '{username_selector}' was not visible.",
                )
        else:
            username_field = _find_username_field(page, timeout_ms)

        if username_field is None:
            failed_at = "username_field_not_found"
            return _r(
                success=False, url=page.url,
                hint="Could not detect the username/email field. Provide username_selector explicitly.",
            )

        _fill_field(username_field, credentials["username"])
        _steps.append("filled_username")

        # ── Password field ────────────────────────────────────────────────
        if password_selector:
            password_field = page.locator(password_selector).first
            try:
                await password_field.wait_for(state="visible", timeout=timeout_ms)
            except Exception:
                failed_at = "password_selector_not_found"
                return _r(
                    success=False, url=page.url,
                    hint=f"Provided password_selector '{password_selector}' was not visible.",
                )
        else:
            password_field = _find_password_field(page, timeout_ms)

        if password_field is None:
            failed_at = "password_field_not_found"
            return _r(
                success=False, url=page.url,
                hint="Could not detect the password field. Provide password_selector explicitly.",
            )

        _fill_field(password_field, credentials["password"])
        _steps.append("filled_password")

        # ── Submit button ──────────────────────────────────────────────────
        if submit_selector:
            submit_btn = page.locator(submit_selector).first
            try:
                await submit_btn.wait_for(state="visible", timeout=timeout_ms)
            except Exception:
                failed_at = "submit_selector_not_found"
                return _r(
                    success=False, url=page.url,
                    hint=f"Provided submit_selector '{submit_selector}' was not visible.",
                )
        else:
            submit_btn = _submit_form(page, timeout_ms)

        if submit_btn is None:
            failed_at = "submit_button_not_found"
            return _r(
                success=False, url=page.url,
                hint="Could not detect submit button. Provide submit_selector explicitly.",
            )

        # ── CAPTCHA detection before submit ────────────────────────────────
        if _has_visible_captcha(page):
            captcha_detected = True
            _steps.append("captcha_detected_before_submit")
            try:
                STORAGE_DIR.mkdir(parents=True, exist_ok=True)
                _cap_file = STORAGE_DIR / f"{_safe_name}_captcha.png"
                await page.screenshot(path=str(_cap_file), full_page=False)
                captcha_screenshot_path = str(_cap_file)
                _steps.append("captcha_screenshot_taken")
            except Exception:
                pass

        # ── Submit ─────────────────────────────────────────────────────────
        pre_submit_url = page.url
        await submit_btn.click()
        _steps.append("submitted_form")

        # Quick poll for URL change — most logins redirect in <500ms.
        # Capture it before the user closes the browser.
        import asyncio as _asyncio
        _url_changed_quick = False
        for _ in range(8):  # 8 x 250ms = 2 seconds
            await _asyncio.sleep(0.25)
            try:
                if _url_origin_path(page.url) != _url_origin_path(pre_submit_url):
                    _steps.append("url_changed_immediately")
                    _url_changed_quick = True
                    break
            except Exception:
                break  # page disconnected

        # Longer wait for slow / SPA logins
        if not _url_changed_quick:
            _ref_url = pre_submit_url.rstrip("/")
            try:
                await page.wait_for_function(
                    "ref => window.location.href.replace(/\\/$/, '') !== ref",
                    arg=_ref_url, timeout=5000,
                )
                _steps.append("url_changed_after_submit")
            except Exception:
                pass

        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass

        # ── CAPTCHA detection after submit ─────────────────────────────────
        if not captcha_detected and _has_visible_captcha(page):
            captcha_detected = True
            _steps.append("captcha_detected_after_submit")
            try:
                STORAGE_DIR.mkdir(parents=True, exist_ok=True)
                _cap_file = STORAGE_DIR / f"{_safe_name}_captcha.png"
                await page.screenshot(path=str(_cap_file), full_page=False)
                captcha_screenshot_path = str(_cap_file)
                _steps.append("captcha_screenshot_taken")
            except Exception:
                pass

        final_url = page.url
        final_title = await page.title()

        # ── Detect visible error message ───────────────────────────────────
        error_text = None
        for sel in [
            "[role='alert']", ".error", ".flash-error", "#error",
            ".alert-danger", ".alert-error",
        ]:
            try:
                el = page.locator(sel).first
                if await el.is_visible():
                    text = (await el.inner_text()).strip()
                    if len(text) > 5:
                        error_text = text
                        break
            except Exception:
                continue

        # ── Determine success ──────────────────────────────────────────────
        url_changed = _url_origin_path(final_url) != _url_origin_path(pre_submit_url)

        form_fields_disappeared = False
        for _ in range(6):
            try:
                if not await username_field.is_visible() and not await password_field.is_visible():
                    form_fields_disappeared = True
                    break
            except Exception:
                break
            time.sleep(0.5)

        # If the URL changed after submit, login succeeded even if a potential
        # CAPTCHA was detected (e.g. Cloudflare Turnstile loads a widget but
        # doesn't block the login). CAPTCHA only matters if the URL stayed the
        # same — meaning the form was blocked from submitting.
        if url_changed:
            login_succeeded = True
            # Cloudflare Turnstile loads a widget that matches CAPTCHA
            # selectors but doesn't block the login. If the URL changed,
            # the "CAPTCHA" was harmless — clear it so the agent doesn't
            # report a false alarm.
            if captcha_detected:
                captcha_detected = False
                _steps.append("captcha_false_alarm_cleared")
        else:
            login_succeeded = (
                error_text is None
                and form_fields_disappeared
                and not captcha_detected
            )

        if login_succeeded:
            STORAGE_DIR.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=str(storage_path))
            _steps.append("session_state_saved")

            # Take a post-login screenshot so the agent can verify the user
            # is logged in, even if the browser tab is later closed
            try:
                _post_file = STORAGE_DIR / f"{_safe_name}_logged_in.png"
                await page.screenshot(path=str(_post_file), full_page=False)
                login_page_screenshot_path = str(_post_file)
                _steps.append("post_login_screenshot_taken")
            except Exception:
                pass

        return _r(
            success=login_succeeded,
            url=final_url,
            title=final_title,
            error_text=error_text,
        )

    except Exception:
        return _r(
            success=False, hint="An unexpected error occurred.",
        )
    finally:
        # Keep the context open — the user may still be using the browser
        # tab. The singleton browser in tools.py manages the browser
        # lifecycle (auto-recovery on crash, close on MCP shutdown).
        pass
