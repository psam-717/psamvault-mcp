"""
psamvault MCP tools.

These are the tools exposed to AI agents. Each tool is designed so that
the agent can orchestrate credential use without ever seeing the plaintext
value.

Key architecture note:
  All sensitive session values (VEK, tokens, kdf_salt) are stored in the
  OS keychain (macOS Keychain, Windows Credential Manager, Linux Secret
  Service) by the psamvault CLI at login time. The MCP server reads the
  VEK from the keychain on every credential access via session.get_vek().
  No key derivation is needed here — the CLI did all the derivation work
  (HMAC → PBKDF2 → AES-GCM-decrypt) at login time.

browser_login architecture:
  The MCP server directly manages a singleton Playwright Chromium instance.
  No subprocess daemon is needed — Playwright imports live in-process,
  eliminating the fragile process chain that caused connection errors with
  certain MCP clients (e.g. Goose). If the browser crashes during use, the
  next browser_login call auto-recovers by launching a new instance.
"""

import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

from mcp_server import api_client
from mcp_server.crypto import decrypt_credentials
from mcp_server.log import get_logger
from mcp_server.session import (
    get_access_token,
    get_vek,
    is_logged_in,
)

logger = get_logger()

# ── Singleton Playwright browser manager ───────────────────────────────────────
# We keep one Chromium instance alive for the lifetime of the MCP server.
# If it crashes, the next call restarts it automatically.

_BROWSER = None  # playwright.async_api.Browser
_PLAYWRIGHT = None  # playwright.async_api.Playwright
_BROWSER_LOCK = asyncio.Lock()


async def _get_browser():
    """Return the singleton Playwright Chromium instance, starting one if needed.
    
    Auto-recovers: if the browser was closed or crashed, this silently
    launches a new one.
    """
    global _BROWSER, _PLAYWRIGHT

    async with _BROWSER_LOCK:
        try:
            if _BROWSER is not None:
                # Probe whether the browser is still alive
                contexts = _BROWSER.contexts
                if contexts is not None:  # if not crashed
                    return _BROWSER
        except Exception:
            pass  # browser is dead — we'll restart below

        # Launch fresh browser (and Playwright context if needed)
        if _PLAYWRIGHT is None:
            from playwright.async_api import async_playwright
            _PLAYWRIGHT = await async_playwright().start()

        logger.info("launching Chromium browser")
        _BROWSER = await _PLAYWRIGHT.chromium.launch(headless=False)
        logger.info("Chromium browser started")
        return _BROWSER


async def _discard_browser():
    """Close the browser if it exists but don't kill the Playwright context.
    
    Call this when a login flow fails in a way that suggests the browser
    is in a bad state. The next _get_browser() call will open a fresh one.
    """
    global _BROWSER
    async with _BROWSER_LOCK:
        if _BROWSER is not None:
            try:
                await _BROWSER.close()
            except Exception:
                pass
            _BROWSER = None


async def close_all_browsers() -> None:
    """Force-close the singleton browser and Playwright context.
    
    Called from main.py when the MCP server shuts down to ensure no
    orphaned Chromium processes remain.
    """
    global _BROWSER, _PLAYWRIGHT
    async with _BROWSER_LOCK:
        try:
            if _BROWSER is not None:
                await _BROWSER.close()
        except Exception:
            pass
        _BROWSER = None
        try:
            if _PLAYWRIGHT is not None:
                await _PLAYWRIGHT.stop()
        except Exception:
            pass
        _PLAYWRIGHT = None
    logger.info("browser and Playwright context closed")


# ── Shared helpers ────────────────────────────────────────────────────────────


async def _decrypt_site_credential(site_name: str) -> dict:
    """Fetch the encrypted vault entry and decrypt locally using the VEK."""
    access_token = get_access_token()
    entry = await api_client.get_vault_entry(access_token, site_name)
    vek = get_vek()
    return decrypt_credentials(
        vek=vek,
        encrypted_blob=entry["encrypted_blob"],
        iv=entry["iv"],
    )


# ── Tool functions ────────────────────────────────────────────────────────────


async def list_vault_sites() -> dict:
    """List all sites stored in the vault."""
    if not is_logged_in():
        return {"error": "Not logged in. Run 'psamvault login' in your terminal first"}
    try:
        access_token = get_access_token()
        entries = await api_client.list_vault_entries(access_token)
        return {
            "sites": [
                {"site_name": e["site_name"], "username_hint": e.get("username_hint")}
                for e in entries
            ],
            "total": len(entries),
        }
    except Exception as e:
        return {"error": str(e)}


async def check_credential_exists(site_name: str) -> dict:
    """Check whether a credential is stored for a given site."""
    if not is_logged_in():
        return {"error": "Not logged in. Run 'psamvault login' in your terminal first."}
    try:
        access_token = get_access_token()
        return await api_client.check_site_exists(access_token, site_name)
    except Exception as e:
        return {"error": str(e)}


async def get_username_for_site(site_name: str) -> dict:
    """Return the username (not password) stored for a site."""
    if not is_logged_in():
        return {"error": "Not logged in. Run 'psamvault login' in your terminal first."}
    try:
        credentials = await _decrypt_site_credential(site_name)
        return {"site_name": site_name, "username": credentials["username"]}
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
    """Open a visible browser and securely log into a site.

    Uses the singleton Playwright browser directly — no subprocess daemon.
    The credential is decrypted locally and typed into the browser fields.
    The credential value is NEVER returned to the caller.
    """
    if not is_logged_in():
        return {"error": "Not logged in. Run 'psamvault login' in your terminal first."}

    # Reject non-http(s) schemes
    if login_url:
        _parsed_login = urlparse(login_url)
        if _parsed_login.scheme not in ("http", "https"):
            return {
                "error": f"login_url scheme '{_parsed_login.scheme}' is not allowed. Only http and https are permitted.",
            }

    # Decrypt the credential upfront
    # in parallel with the decrypt.
    try:
        credentials = await _decrypt_site_credential(site_name)
    except Exception as e:
        return {"success": False, "error": f"Failed to decrypt credential: {e}"}

    # Get or start the singleton browser
    try:
        browser = await _get_browser()
    except Exception as e:
        logger.error("failed to launch browser: %s", e)
        return {"success": False, "error": f"Failed to launch browser: {e}"}

    # Run the login flow — import here so it's only loaded when browser_login is called
    from mcp_server.browser_login_flow import run_login_flow

    try:
        result = await run_login_flow(
            browser=browser,
            site_name=site_name,
            credentials=credentials,
            login_url=login_url,
            username_selector=username_selector,
            password_selector=password_selector,
            submit_selector=submit_selector,
            timeout_ms=timeout_ms,
        )
        return result
    except Exception as e:
        logger.error("browser_login flow failed: %s", e)
        # If the browser crashed, discard so next call restarts fresh
        await _discard_browser()
        return {
            "success": False,
            "error": f"Login flow failed: {e}",
            "failed_at": "unexpected_error",
        }
