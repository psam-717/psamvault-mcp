"""
psamvault MCP tools.
 
These are the tools exposed to AI agents. Each tool is designed so that
the agent can orchestrate credential use without ever seeing the plaintext
value. The consent gate in each tool ensures the user approves every access.
 
Key architecture note:
  The session file at ~/.psamvault/session.json contains the raw VEK
  (Vault Encryption Key) produced by the CLI at login. The MCP server
  reads this VEK and uses it directly to decrypt vault entries locally
  with AES-256-GCM. No key derivation is needed here — the CLI did all
  the derivation work (HMAC → PBKDF2 → AES-GCM-decrypt) at login time.
"""


from mcp_server import api_client, consent
from mcp_server.crypto import decrypt_credentials
from mcp_server.session import (
    get_access_token,
    get_vek,
    is_logged_in
)


# helper - fetch and decrypt a vault entry using the session VEK

async def _decrypt_site_credential(site_name: str) -> dict:
    """
    Fetch the encrypted vault entry from the API and decrypt it locally
    using the VEK from the session file.

    The VEK is read fresh from the session file on every call so that
    if the user logs out mid-session the VEK is no longer accessible.

    Returns:
        {"username": str, "password": str, "notes": str}
        Held in memory only — never logged or persisted.

    Raises:
        RuntimeError: If not logged in or VEK not in session.
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


# Tool functions
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
            "error": "Not logged in. Run 'psamvault login' int your terminal first"
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
) -> dict:
    """
    Make an authenticated HTTP request using a stored credential.
 
    Flow:
      1. Show user a consent prompt — blocked without approval
      2. Read VEK from ~/.psamvault/session.json
      3. Fetch encrypted blob from psamvault API
      4. Decrypt locally with VEK using AES-256-GCM
      5. Pass plaintext credential to backend proxy over HTTPS
      6. Backend injects credential into outbound request
      7. Return HTTP response to agent — credential never appears
 
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
 
    Returns:
        {"status_code": int, "response_body": str, "site_name": str,
         "injected_as": str, "target_url": str}
        or {"error": str} if denied or something fails.
    """
    if not is_logged_in():
        return {
            "error": "Not logged in. Run  psamvault login  in your terminal first."
        }
        
    
    # User consent - mandatory gate before any credential is accessed
    approved = consent.request_consent(
        site_name=site_name,
        target_url=target_url,
        inject_as=inject_as,
        agent_description="AI agent via psamvault MCP"
    )
    
    if not approved:
        return {
            "error": (
                f"Access denied by user. "
                f"Credential for '{site_name}' was not used'"
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
        
        consent.notify_completion(
            site_name=site_name,
            status_code=result["status_code"],
            target_url=target_url
        )
        
        return result
    
    except Exception as e:
        return {"error": f"Proxy request failed: {str(e)}"}
    

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
        
    approved = consent.request_consent(
        site_name=site_name,
        target_url="(username only — no HTTP request will be made)",
        inject_as="username_only",
        agent_description="AI agent via psamvault MCP",
    )
    
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


async def debug_dump_credential(site_name: str) -> dict:
    """
    DIAGNOSTIC TOOL — writes the decrypted username and password for a site
    to a plaintext file on the local machine so you can verify credential
    retrieval works independently of the browser flow.

    The file is written to the current user's home directory:
        ~/psamvault_debug_dump.txt

    ⚠️  Delete this file after testing. It contains plaintext credentials.

    Args:
        site_name: The vault site to dump, e.g. "github.com".

    Returns:
        {"file_path": str, "site_name": str, "written": bool}
        or {"error": str}
    """
    import pathlib

    if not is_logged_in():
        return {"error": "Not logged in. Run  psamvault login  in your terminal first."}

    approved = consent.request_consent(
        site_name=site_name,
        target_url="local file ~/psamvault_debug_dump.txt",
        inject_as="debug_file_dump",
        agent_description="AI agent via psamvault MCP (DIAGNOSTIC)",
    )

    if not approved:
        return {"error": f"Access denied by user."}

    try:
        credentials = await _decrypt_site_credential(site_name)
    except RuntimeError as e:
        return {"error": str(e)}
    except Exception as e:
        return {
            "error": (
                f"Could not decrypt credential for '{site_name}'. "
                f"Detail: {str(e)}"
            )
        }

    dump_path = pathlib.Path.home() / "psamvault_debug_dump.txt"
    try:
        dump_path.write_text(
            f"psamvault diagnostic dump\n"
            f"=========================\n"
            f"site_name : {site_name}\n"
            f"username  : {credentials['username']}\n"
            f"password  : {credentials['password']}\n"
            f"\n⚠️  This file will be deleted automatically in 60 seconds.\n",
            encoding="utf-8",
        )
    except Exception as e:
        return {"error": f"Failed to write file: {str(e)}"}

    # Auto-delete after 60 seconds so the agent cannot read it later.
    import asyncio as _asyncio

    async def _auto_delete(path: pathlib.Path, delay: int = 60) -> None:
        await _asyncio.sleep(delay)
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass

    _asyncio.create_task(_auto_delete(dump_path, delay=60))

    return {
        "site_name": site_name,
        "file_path": str(dump_path),
        "written": True,
        "warning": (
            "Plaintext credentials on disk — the file will auto-delete in 60 seconds. "
            "Do NOT read this file programmatically; it is for human verification only."
        ),
    }


async def browser_login(
    site_name: str,
    login_url: str | None = None,
    username_selector: str | None = None,
    password_selector: str | None = None,
    submit_selector: str | None = None,
) -> dict:
    """
    Open a headed browser and handle the full login flow for a site — including
    auto-discovering the login page and multi-step authentication flows
    (e.g., click "Continue with Email" → fill email → click Next → fill password → submit).

    If login_url is not provided, Playwright navigates to the site homepage,
    finds the sign-in link, clicks it, and uses the resulting URL as the login page.

    The plaintext credentials are decrypted locally using the VEK and are
    used only to drive the browser. They are never returned to the agent.

    All parameters except site_name are optional.

    Args:
        site_name:         Vault site whose credential to use, e.g. "github.com".
        login_url:         Full URL of the login page (optional — auto-discovered if omitted).
        username_selector: CSS selector for the username/email field (optional).
        password_selector: CSS selector for the password field (optional).
        submit_selector:   CSS selector for the final submit button (optional).

    Returns:
        {
            "success":    bool,
            "url":        str   — final page URL after form submission,
            "title":      str   — final page title after form submission,
            "error_text": str | None — visible error message if login failed
        }
    """
    import asyncio
    from playwright.async_api import async_playwright

    # ── Candidate selector lists ──────────────────────────────────────────
    _LOGIN_LINK_CANDIDATES = [
        "a:has-text('Sign in')",
        "a:has-text('Log in')",
        "a:has-text('Login')",
        "a:has-text('Sign In')",
        "a:has-text('Log In')",
        "button:has-text('Sign in')",
        "button:has-text('Log in')",
        "button:has-text('Login')",
        "[href*='login' i]",
        "[href*='signin' i]",
        "[href*='sign-in' i]",
    ]
    _GATEWAY_BUTTONS = [
        "button:has-text('Continue with Email')",
        "button:has-text('Sign in with email')",
        "button:has-text('Use email')",
        "a:has-text('Continue with Email')",
        "a:has-text('Sign in with email')",
        "[data-provider='email']",
        "button:has-text('Email')",
    ]
    _USERNAME_CANDIDATES = [
        "input[type='email']",
        "input[type='text'][name*='email' i]",
        "input[type='text'][id*='email' i]",
        "input[type='text'][name*='user' i]",
        "input[type='text'][id*='user' i]",
        "input[type='text'][id*='login' i]",
        "input[type='text']",
    ]
    _NEXT_STEP_CANDIDATES = [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Continue')",
        "button:has-text('Next')",
    ]
    _SUBMIT_CANDIDATES = [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Sign in')",
        "button:has-text('Log in')",
        "button:has-text('Login')",
        "button:has-text('Continue')",
        "button:has-text('Submit')",
    ]
    _ERROR_SELECTORS = [
        "[role='alert']", ".error", ".flash-error", "#error",
        ".alert-danger", ".alert-error",
        "[class*='error' i]", "[class*='invalid' i]",
    ]
    # ─────────────────────────────────────────────────────────────────────

    if not is_logged_in():
        return {"error": "Not logged in. Run  psamvault login  in your terminal first."}

    # Consent is shown before the browser opens — use site_name if url not yet known
    approved = consent.request_consent(
        site_name=site_name,
        target_url=login_url or f"https://{site_name}",
        inject_as="browser_form_fill",
        agent_description="AI agent via psamvault MCP",
    )

    if not approved:
        return {"error": f"Access denied by user. Credential for '{site_name}' was not used."}

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

    browser = None
    # Protect the MCP stdio transport: Chromium can write to stdout on startup,
    # which corrupts the JSON-RPC stream. Redirect stdout to stderr for the
    # duration of the Playwright session.
    import sys as _sys
    _saved_stdout = _sys.stdout
    _sys.stdout = _sys.stderr
    try:
        async with async_playwright() as p:
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
            page = await browser.new_page()

            # ── Helper: poll candidates until one is visible ──────────────
            async def wait_for_visible(candidates: list, timeout_ms: int = 6000) -> str | None:
                loop = asyncio.get_running_loop()
                deadline = loop.time() + timeout_ms / 1000
                while loop.time() < deadline:
                    for sel in candidates:
                        try:
                            if await page.locator(sel).first.is_visible():
                                return sel
                        except Exception:
                            continue
                    await asyncio.sleep(0.25)
                return None
            # ─────────────────────────────────────────────────────────────

            # ── Phase 0: Discover the login URL if not provided ───────────
            if login_url:
                await page.goto(login_url, wait_until="domcontentloaded")
            else:
                # Navigate to the site homepage and find the sign-in link
                homepage = f"https://{site_name}"
                await page.goto(homepage, wait_until="domcontentloaded")

                login_link = await wait_for_visible(_LOGIN_LINK_CANDIDATES, timeout_ms=5000)
                if login_link:
                    await page.locator(login_link).first.click()
                    await page.wait_for_load_state("domcontentloaded")
                    login_url = page.url
                else:
                    return {
                        "error": (
                            f"Could not find a sign-in link on {homepage}. "
                            "Please provide login_url explicitly."
                        )
                    }
            # ─────────────────────────────────────────────────────────────

            # ── Phase 1: Locate the username/email field ──────────────────
            username_sel = username_selector
            if username_sel:
                found = await wait_for_visible([username_sel])
                if not found:
                    return {"error": f"Provided username_selector '{username_sel}' was not found or not visible."}
            else:
                # Try immediate detection first (traditional single-page form)
                username_sel = await wait_for_visible(_USERNAME_CANDIDATES, timeout_ms=2000)

                if not username_sel:
                    # Field not visible — look for a gateway button (e.g. "Continue with Email")
                    gateway = await wait_for_visible(_GATEWAY_BUTTONS, timeout_ms=4000)
                    if gateway:
                        await page.locator(gateway).first.click()
                        # Wait for the email/username field to appear after clicking
                        username_sel = await wait_for_visible(_USERNAME_CANDIDATES, timeout_ms=6000)

            if not username_sel:
                return {
                    "error": (
                        "Could not detect the username/email field. "
                        "The page may need a gateway button click that was not recognised, "
                        "or it uses a non-standard login flow. "
                        "Try providing username_selector explicitly."
                    )
                }

            # ── Fill email/username ───────────────────────────────────────
            username_field = page.locator(username_sel).first
            await username_field.click()
            await username_field.fill(credentials["username"])
            await username_field.press("Tab")
            await asyncio.sleep(0.3)  # brief pause — some sites react to input events

            # ── Phase 2: Locate the password field ───────────────────────
            password_sel = password_selector
            if password_sel:
                found = await wait_for_visible([password_sel])
                if not found:
                    return {"error": f"Provided password_selector '{password_sel}' was not found or not visible."}
            else:
                # Check if password is already on this step (single-page form)
                password_sel = await wait_for_visible(["input[type='password']"], timeout_ms=1500)

                if not password_sel:
                    # Multi-step: need to click Next/Continue to reveal the password field
                    next_btn = await wait_for_visible(_NEXT_STEP_CANDIDATES, timeout_ms=3000)
                    if next_btn:
                        await page.locator(next_btn).first.click()
                        password_sel = await wait_for_visible(["input[type='password']"], timeout_ms=6000)

            if not password_sel:
                return {
                    "error": (
                        "Could not detect the password field. "
                        "The site may use a non-standard multi-step flow. "
                        "Try providing password_selector explicitly."
                    )
                }

            # ── Fill password ─────────────────────────────────────────────
            password_field = page.locator(password_sel).first
            await password_field.click()
            await password_field.fill(credentials["password"])
            await password_field.press("Tab")
            await asyncio.sleep(0.3)

            # ── Phase 3: Locate and click the submit button ───────────────
            submit_sel = submit_selector
            if not submit_sel:
                submit_sel = await wait_for_visible(_SUBMIT_CANDIDATES, timeout_ms=3000)

            if not submit_sel:
                return {"error": "Could not detect the submit button. Please provide submit_selector."}

            await page.locator(submit_sel).first.click()

            # Wait for initial navigation (CAPTCHA or 2FA may appear at this point).
            # Use a relaxed timeout — the page may not reach networkidle until the
            # user completes any challenge. We intentionally keep the browser open.
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass  # timeout is fine — CAPTCHA/2FA still in progress

            final_url = page.url
            final_title = await page.title()

            # ── Detect visible error message on resulting page ────────────
            error_text = None
            for sel in _ERROR_SELECTORS:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible():
                        error_text = (await el.inner_text()).strip()
                        break
                except Exception:
                    continue

            # ── Keep browser open for the user ───────────────────────────
            # The user may need to complete a CAPTCHA, 2FA challenge, or simply
            # continue their session. The browser stays open until the user
            # closes it (or up to 10 minutes, whichever comes first).
            try:
                await page.wait_for_event("close", timeout=600_000)
            except Exception:
                pass  # page already closed or timeout reached
            # ─────────────────────────────────────────────────────────────

            return {
                "success": error_text is None,
                "url": final_url,
                "title": final_title,
                "error_text": error_text,
            }
    except Exception as e:
        return {"error": f"Browser login failed: {str(e)}"}
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass  # already closed by the user
        _sys.stdout = _saved_stdout