"""
psamvault MCP tools.

These are the tools exposed to AI agents. Each tool is designed so that
the agent can orchestrate credential use without ever seeing the plaintext
value.

Tools are organised into 3 groups:

  🛠  Entry & Orientation (get_version, search_vault_tools)
      — always start here to discover what to use.

  🔐  Site Authentication (list_vault_sites, check_credential_exists,
                            get_username_for_site, browser_login)
      — end-to-end: discover, check, and log into websites.

  🔑  API Key Operations (list_api_keys, use_credential,
                           run_with_credential, scan_and_protect,
                           capture_stripe_credentials)
      — all tools that deal with API keys: discover, use, inject, and protect.

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
import base64
import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from mcp_server import api_client
from mcp_server.crypto import decrypt_credentials, decrypt_api_key
from mcp_server.log import get_logger
from mcp_server.session import (
    get_access_token,
    get_vek,
    is_logged_in,
)

# Private / loopback IP blocks blocked as SSRF protection
_PRIVATE_IP_BLOCKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]

# Response headers that must never reach the agent context
_SENSITIVE_RESPONSE_HEADERS = frozenset({
    "set-cookie",
    "authorization",
    "www-authenticate",
    "proxy-authenticate",
    "set-cookie2",
})


def _reject_internal_target(url: str) -> str | None:
    """Return an error message if *url* targets a loopback or private IP.

    Returns ``None`` when the target is considered safe (public hostname
    or public IP literal).  This prevents SSRF — an attacker who tricks
    the agent into calling ``use_credential`` with a crafted ``target_url``
    cannot make the server send authenticated requests to internal services.

    Hostname literals (``localhost``, ``*.local``, ``*.internal``) are
    blocked immediately.  IP literals are checked against well-known
    private and link-local ranges.
    """
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").strip()

    if not hostname:
        return "target_url has no hostname"

    lower = hostname.lower()

    # --- Block known internal-only hostnames ---
    if lower in ("localhost", "localhost.localdomain",
                 "127.0.0.1", "::1", "0.0.0.0",
                 "[::1]"):
        return (
            f"target_url host '{hostname}' is a loopback address — "
            "SSRF protection blocked the request"
        )

    if lower.endswith(".local") or lower.endswith(".internal"):
        return (
            f"target_url host '{hostname}' is an internal-only domain "
            "(.local / .internal) — SSRF protection blocked the request"
        )

    # --- If the hostname is a literal IP, check against private blocks ---
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        addr = None

    if addr is not None:
        for block in _PRIVATE_IP_BLOCKS:
            if addr in block:
                return (
                    f"target_url host '{hostname}' is a private IP address "
                    f"(within {block}) — SSRF protection blocked the request"
                )
        # Public IP literal — allowed.

    return None

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


async def list_api_keys(project_name: str | None = None) -> dict:
    """List all stored API keys — names and service hints only, never key values.

    When project_name is provided, only keys belonging to that project
    (stored as ``project_name/.env/KEY_NAME``) are returned.

    Args:
        project_name: Optional project name to filter by. Keys stored via
                      scan_and_protect(project_name="...") use the format
                      ``project/.env/KEY`` — pass the project name here
                      to see only those keys.
    """
    if not is_logged_in():
        return {"error": "Not logged in. Run 'psamvault login' in your terminal first"}
    try:
        access_token = get_access_token()
        entries = await api_client.list_api_key_entries(access_token)

        # Build the response items
        items = []
        for e in entries:
            name = e["name"]
            # Check if this key belongs to a project (format: project/.env/KEY)
            parts = name.split("/.env/")
            is_project_key = len(parts) == 2

            if project_name:
                # Filter: only keys whose project prefix matches
                if not is_project_key or parts[0] != project_name:
                    continue

            items.append({
                "name": name,
                "service_hint": e.get("service_hint"),
                "notes": e.get("notes"),
                "created_at": e.get("created_at"),
                "project": parts[0] if is_project_key else None,
                "key_name": parts[1] if is_project_key else name,
            })

        # Group by project for display
        projects: dict[str, list] = {}
        standalone: list = []
        for item in items:
            if item["project"]:
                projects.setdefault(item["project"], []).append(item)
            else:
                standalone.append(item)

        return {
            "api_keys": items,  # flat list (backwards-compatible)
            "total": len(items),
            "projects": projects,  # grouped by project
            "standalone": standalone,  # keys with no project
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

        # Verify login_url host belongs to the same domain as site_name
        # (prevent credential phishing via prompt injection)
        _login_host = (_parsed_login.hostname or "").lower()
        _site_host = site_name.lower()
        if _login_host != _site_host and not _login_host.endswith("." + _site_host):
            return {
                "error": (
                    f"login_url host '{_parsed_login.hostname}' does not match "
                    f"site_name '{site_name}'. The login URL must belong "
                    "to the same domain."
                ),
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


# ── use_credential tool ────────────────────────────────────────────────────


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
    """Fetch a credential (vault entry or API key), decrypt locally, make an
    authenticated HTTP request, and return the response — NEVER exposing the
    credential value back to the caller.

    The credential is looked up first in the API key entries, then in the
    vault entries (site passwords). This allows both API keys and site login
    passwords to be used for HTTP requests.

    Args:
        site_name:    Name of the credential to use (API key name or site name).
        target_url:   URL to make the authenticated request to.
        method:       HTTP method (GET, POST, PUT, PATCH, DELETE, etc.).
        inject_as:    How to inject the credential into the request.
                      "bearer_token" (default): Authorization: Bearer &lt;value&gt;
                      "api_key_header": <header_name>: <value>
                      "basic_auth": Authorization: Basic base64(user:pass)
        header_name:  Required when inject_as="api_key_header".
        body:         JSON body for POST/PUT/PATCH requests.
        extra_headers: Additional headers to include.
        fields:       If set, filter the JSON response to only these keys.
    """
    if not is_logged_in():
        return {"error": "Not logged in. Run 'psamvault login' in your terminal first."}

    # Validate method
    method = method.upper()

    try:
        access_token = get_access_token()
        vek = get_vek()

        encrypted_entry = None
        entry_type = None  # "api_key" or "vault"

        # Try API key entries first
        try:
            encrypted_entry = await api_client.get_api_key_entry(access_token, site_name)
            entry_type = "api_key"
        except Exception:
            # Fall through to vault entries
            pass

        # Fallback to vault entries
        if encrypted_entry is None:
            try:
                encrypted_entry = await api_client.get_vault_entry(access_token, site_name)
                entry_type = "vault"
            except Exception:
                return {
                    "error": f"Credential '{site_name}' not found in API keys or vault entries."
                }

        # Decrypt locally
        if entry_type == "api_key":
            decrypted = decrypt_api_key(
                vek=vek,
                encrypted_blob=encrypted_entry["encrypted_blob"],
                iv=encrypted_entry["iv"],
            )
            credential_value = decrypted["api_key"]
        else:
            decrypted = decrypt_credentials(
                vek=vek,
                encrypted_blob=encrypted_entry["encrypted_blob"],
                iv=encrypted_entry["iv"],
            )
            credential_value = decrypted["password"]

        # Build request headers
        headers = {}

        # Add extra headers (if provided)
        if extra_headers:
            headers.update(extra_headers)

        # Inject credential based on mode
        if inject_as == "bearer_token":
            headers["Authorization"] = f"Bearer {credential_value}"

        elif inject_as == "api_key_header":
            if not header_name:
                return {"error": "header_name is required when inject_as='api_key_header'."}
            headers[header_name] = credential_value

        elif inject_as == "basic_auth":
            if entry_type == "vault":
                username = decrypted.get("username", "")
                password = decrypted.get("password", "")
            else:
                # For API key entries used as basic auth, use service as username
                username = decrypted.get("service", "")
                password = decrypted.get("api_key", "")
            basic = base64.b64encode(f"{username}:{password}".encode()).decode()
            headers["Authorization"] = f"Basic {basic}"

        else:
            return {"error": f"Unknown inject_as mode: '{inject_as}'. Use 'bearer_token', 'api_key_header', or 'basic_auth'."}

        # SSRF guard — reject requests to loopback, private, or link-local IPs
        ssrf_error = _reject_internal_target(target_url)
        if ssrf_error:
            return {"error": ssrf_error}

        # Make the authenticated HTTP request
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method=method,
                url=target_url,
                headers=headers,
                json=body if method in ("POST", "PUT", "PATCH") else None,
                timeout=30.0,
                follow_redirects=True,
            )

        # Parse response body
        try:
            response_data = response.json()
        except Exception:
            response_data = response.text

        # Filter response by fields if requested
        if fields and isinstance(response_data, dict):
            response_data = {k: v for k, v in response_data.items() if k in fields}
        elif fields and isinstance(response_data, list):
            response_data = [
                {k: v for k, v in item.items() if k in fields}
                for item in response_data
            ]

        return {
            "success": True,
            "status_code": response.status_code,
            "headers": {
                k: v
                for k, v in response.headers.items()
                if k.lower() not in _SENSITIVE_RESPONSE_HEADERS
            },
            "data": response_data,
        }

    except Exception as e:
        logger.error("use_credential failed: %s", e)
        return {"error": f"use_credential failed: {e}"}


# ── scan_and_protect tool ──────────────────────────────────────────────────


async def scan_and_protect(
    project_dir: str | None = None,
    patterns: list[str] | None = None,
    project_name: str | None = None,
) -> dict:
    """Scan a project directory for exposed secrets in .env files and protect them.

    Discovers .env files, detects API keys and secrets using pattern matching,
    encrypts them into the psamvault API key store, and replaces the plaintext
    values with "psamvault:<KEY_NAME>" placeholders.

    When project_name is provided, keys are stored as
    ``project_name/.env/KEY_NAME`` — this groups them under a project namespace
    for cleaner listing and filtering. When omitted, keys are stored as
    ``env/.env/KEY_NAME`` (backwards-compatible).

    Args:
        project_dir: Path to the project directory (defaults to CWD).
        patterns:    Optional custom key name patterns to scan for
                     (e.g. ["MY_CUSTOM_KEY"]). Added on top of built-in patterns.
        project_name: Optional project name for grouping. Keys stored as
                      ``project_name/.env/KEY_NAME``.

    Returns:
        A dict with: scanned_dir, files_scanned, secrets_found, captured,
        files_modified, errors.
    """
    from mcp_server.env_scanner import scan_project
    from mcp_server.crypto import encrypt_api_key
    from mcp_server import api_client as ac

    if not is_logged_in():
        return {"error": "Not logged in. Run 'psamvault login' in your terminal first."}

    # Default to CWD if no directory specified
    if project_dir is None:
        project_dir = str(Path.cwd())

    try:
        # Scan the project
        scan_result = scan_project(project_dir)

        if scan_result["secrets_found"] == 0:
            return {
                "scanned_dir": scan_result["scanned_dir"],
                "files_scanned": scan_result["files_scanned"],
                "secrets_found": 0,
                "already_protected": scan_result["already_protected"],
                "message": "No unprotected secrets found. Everything is clean!"
                if scan_result["already_protected"] > 0
                else "No secrets detected in .env files.",
                "files_not_gitignored": scan_result["files_not_gitignored"],
            }

        # Capture each candidate into the vault
        access_token = get_access_token()
        vek = get_vek()
        captured = []
        errors = []
        files_modified = set()

        for candidate in scan_result["candidates"]:
            if candidate["already_protected"]:
                continue

            try:
                # Read the actual full value from the file
                env_path = None
                for f in scan_result["files_scanned"]:
                    if f.endswith(candidate["file"]):
                        env_path = f
                        break
                if not env_path:
                    continue

                lines = Path(env_path).read_text(encoding="utf-8", errors="replace").splitlines()
                line = lines[candidate["index"]]
                # Extract the value after KEY=
                match = re.match(r"(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*(.*)", line)
                if not match:
                    continue
                raw_value = match.group(1).strip()
                # Strip quotes
                if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in ('"', "'"):
                    raw_value = raw_value[1:-1]

                # Encrypt the key value
                service_hint = candidate.get("detected_by", "env_scanner")[:255]
                encrypted_blob, iv = encrypt_api_key(
                    vek=vek,
                    service=service_hint,
                    api_key=raw_value,
                    notes=f"Auto-captured from {candidate['file']} at line {candidate['index'] + 1}",
                )

                # Store in vault via API
                if project_name:
                    vault_name = f"{project_name}/.env/{candidate['key']}"
                else:
                    vault_name = f"env/{candidate['file']}/{candidate['key']}"
                await ac.add_api_key_entry(
                    access_token=access_token,
                    name=vault_name,
                    service_hint=service_hint,
                    encrypted_blob=encrypted_blob,
                    iv=iv,
                )

                # Replace in .env file
                old_line = lines[candidate["index"]]
                new_line = re.sub(
                    r"(=)(\s*).*",
                    lambda m: f"{m.group(1)}{m.group(2)}psamvault:{candidate['key']}",
                    old_line,
                )
                lines[candidate["index"]] = new_line
                Path(env_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
                files_modified.add(candidate["file"])

                captured.append({
                    "key": candidate["key"],
                    "file": candidate["file"],
                    "stored_as": vault_name,
                    "confidence": candidate["confidence"],
                })

            except Exception as e:
                logger.error("Failed to capture %s: %s", candidate["key"], e)
                errors.append({"key": candidate["key"], "file": candidate["file"], "error": str(e)})

        return {
            "scanned_dir": scan_result["scanned_dir"],
            "files_scanned": scan_result["files_scanned"],
            "secrets_found": scan_result["secrets_found"],
            "already_protected": scan_result["already_protected"],
            "captured": captured,
            "captured_count": len(captured),
            "files_modified": sorted(files_modified),
            "errors": errors if errors else None,
            "files_not_gitignored": scan_result["files_not_gitignored"],
            "message": f"Captured {len(captured)} secrets into psamvault. "
                       f"Plaintext values replaced with psamvault: placeholders."
                       + (f" {len(errors)} errors." if errors else ""),
        }

    except Exception as e:
        logger.error("scan_and_protect failed: %s", e)
        return {"error": f"scan_and_protect failed: {e}"}


# ── capture_stripe_credentials ─────────────────────────────────────────────


async def capture_stripe_credentials(
    provider: str,
    project_dir: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Capture credentials provisioned by Stripe Projects into psamvault.

    After an agent runs ``stripe projects add <provider>``, the provisioned
    credentials land in the project's ``.env`` file as plaintext. This tool:
    1. Runs ``stripe projects env --pull`` to sync fresh credentials.
    2. Parses ``.env`` for secrets using pattern matching.
    3. Encrypts each secret with the VEK and stores it in the psamvault
       API key store under ``stripe/<provider>/<KEY_NAME>``.
    4. Replaces plaintext values with ``psamvault:<KEY_NAME>`` placeholders.

    Args:
        provider:    The Stripe Projects provider, e.g. ``"neon"``,
                     ``"supabase"``, ``"openrouter"``.
        project_dir: Project directory (defaults to CWD).
        dry_run:     If ``True``, only preview what would be captured.

    Returns:
        Dict with: success, provider, project_dir, env_file, captured,
        captured_count, files_modified, errors, message, stripe_output.
    """
    from mcp_server.stripe_capture import capture_stripe_credentials as _capture

    if not is_logged_in():
        return {"error": "Not logged in. Run 'psamvault login' in your terminal first."}

    try:
        result = await _capture(
            provider=provider,
            project_dir=project_dir or str(Path.cwd()),
            dry_run=dry_run,
        )
        return result
    except Exception as e:
        logger.error("capture_stripe_credentials failed: %s", e)
        return {"error": f"capture_stripe_credentials failed: {e}"}


# ── run_with_credential tool ─────────────────────────────────────────────


async def run_with_credential(
    site_name: str,
    command: str,
    inject_as: str = "env",
    env_var_name: str | None = None,
    extra_env: dict | None = None,
    workdir: str | None = None,
    timeout: int = 120,
) -> dict:
    """Run a shell command with a credential injected via env var or stdin.

    The credential value NEVER enters the agent's context — it is decrypted
    locally, injected into the subprocess, and all output is scanned for the
    value and redacted before being returned.

    The credential is looked up first in API key entries, then vault entries.

    Args:
        site_name:    The credential to use (API key name or vault site name).
        command:      Shell command to run with the credential injected.
        inject_as:    ``"env"`` (default) — set credential as environment var.
                      ``"stdin"`` — pipe credential as stdin to the process.
        env_var_name: Required when ``inject_as="env"``. The environment
                      variable name to set the credential as.
        extra_env:    Optional extra env vars (non-sensitive). Merged into
                      the subprocess environment alongside the credential.
        workdir:      Working directory for the subprocess. Defaults to
                      the MCP server's CWD.
        timeout:      Max seconds to wait (default 120). Set higher for
                      long-running operations like uploads or builds.

    Returns:
        A dict with keys:
        - ``exit_code``: int — subprocess exit code (-1 for errors)
        - ``stdout``: str — stdout with credential value redacted
        - ``stderr``: str — stderr with credential value redacted
        - ``error``: str — error details if command could not run
    """
    if not is_logged_in():
        return {"error": "Not logged in. Run 'psamvault login' in your terminal first."}

    try:
        access_token = get_access_token()
        vek = get_vek()

        # Try API key entries first, then vault entries
        encrypted_entry = None
        entry_type = None

        try:
            encrypted_entry = await api_client.get_api_key_entry(access_token, site_name)
            entry_type = "api_key"
        except Exception:
            pass

        if encrypted_entry is None:
            try:
                encrypted_entry = await api_client.get_vault_entry(access_token, site_name)
                entry_type = "vault"
            except Exception:
                return {
                    "error": f"Credential '{site_name}' not found in API keys or vault entries."
                }

        # Decrypt locally
        if entry_type == "api_key":
            decrypted = decrypt_api_key(
                vek=vek,
                encrypted_blob=encrypted_entry["encrypted_blob"],
                iv=encrypted_entry["iv"],
            )
            credential_value = decrypted["api_key"]
        else:
            decrypted = decrypt_credentials(
                vek=vek,
                encrypted_blob=encrypted_entry["encrypted_blob"],
                iv=encrypted_entry["iv"],
            )
            credential_value = decrypted["password"]

        # SSRF guard — scan command for URLs targeting internal addresses.
        # An attacker who tricks the agent into running a command like
        #   curl http://169.254.169.254/$KEY
        # would exfiltrate the credential to an internal endpoint.
        # We extract all http(s) URLs from the command string and reject
        # any that point to loopback, private, or link-local addresses.
        _url_re = re.compile(r"https?://[^\s\"'<>|;&$`!(){}]+")
        for _match in _url_re.finditer(command):
            _url = _match.group(0)
            _ssrf_err = _reject_internal_target(_url)
            if _ssrf_err:
                return {
                    "error": (
                        f"Command contains a URL targeting an internal address: "
                        f"'{_url[:80]}'. {_ssrf_err}"
                    ),
                }

        # Run command with credential injected
        from mcp_server.cmd_runner import run_command_with_credential as _run_cmd

        result = await _run_cmd(
            command=command,
            credential_value=credential_value,
            inject_as=inject_as,
            env_var_name=env_var_name,
            extra_env=extra_env,
            workdir=workdir,
            timeout=timeout,
        )
        return result

    except Exception as e:
        logger.error("run_with_credential failed: %s", e)
        return {"error": f"run_with_credential failed: {e}"}
