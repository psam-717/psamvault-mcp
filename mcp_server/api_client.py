import os
from urllib.parse import urlparse

import httpx

from mcp_server.session import get_refresh_token, update_tokens

_BASE_URL_RAW = os.getenv("PSAMVAULT_API_URL", "https://psam-vault-backend.onrender.com")
_parsed = urlparse(_BASE_URL_RAW)
if _parsed.scheme != "https" or not _parsed.netloc:
    raise RuntimeError(
        f"PSAMVAULT_API_URL must use HTTPS. Got scheme '{_parsed.scheme}' "
        f"for '{_BASE_URL_RAW}'."
    )
BASE_URL = _BASE_URL_RAW

def _auth_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


def _handle_error(response: httpx.Response) -> None:
    if not response.is_success:
        try:
            detail = response.json().get("detail", response.text[:500] if response.text else "")
        except (ValueError, httpx.RequestError):
            detail = response.text[:500] if response.text else ""
        raise RuntimeError(f"psamvault API error {response.status_code}: {detail}")


async def _refresh_access_token(refresh_token: str) -> tuple[str, str]:
    """POST /auth/refresh — returns (new_access_token, new_refresh_token)."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{BASE_URL}/auth/refresh",
            json={"refresh_token": refresh_token},
            timeout=30.0,
        )
    _handle_error(response)
    data = response.json()
    return data["access_token"], data["refresh_token"]


async def _refresh_and_retry(retry_fn):
    """
    Refresh the access token using the stored refresh token, persist both
    new tokens to the OS keychain, then retry the original request.
    Raises RuntimeError if the refresh itself fails (session truly expired).
    """
    try:
        refresh_token = get_refresh_token()
        try:
            new_access, new_refresh = await _refresh_access_token(refresh_token)
        except Exception:
            # Refresh tokens are single-use and the CLI, the dashboard and every MCP
            # server share one keychain entry, so a 401 here may just mean another
            # client rotated the chain a moment ago. Re-read the store once and retry
            # with its token; an unchanged (or empty) store means the chain is dead.
            rotated = get_refresh_token()
            if not rotated or rotated == refresh_token:
                raise
            new_access, new_refresh = await _refresh_access_token(rotated)
        update_tokens(new_access, new_refresh)
        result = await retry_fn(new_access)
        if result is None:
            raise RuntimeError("Server returned 401 after token refresh.")
        return result
    except Exception as e:
        raise RuntimeError(
            "Session expired. Run  psamvault login  in your terminal to re-authenticate."
        ) from e


async def list_vault_entries(access_token: str) -> list[dict]:
    """
    GET /vault — return all vault entries as lightweight list items.
    Returns site names and username hints only — no credential values.
    """
    async def _call(token: str):
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{BASE_URL}/vault",
                headers=_auth_headers(token),
                timeout=30.0
            )
        if response.status_code == 401:
            return None
        _handle_error(response)
        return response.json().get("entries", [])

    result = await _call(access_token)
    if result is None:
        return await _refresh_and_retry(_call)
    return result


async def get_vault_entry(access_token: str, site_name: str) -> dict:
    """
    GET /vault/{site_name} — return the encrypted blob and iv for a site.
    The MCP server decrypts this locally before passing to the proxy.
    """
    async def _call(token: str):
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{BASE_URL}/vault/{site_name}",
                headers=_auth_headers(token),
                timeout=30.0,
            )
        if response.status_code == 401:
            return None
        _handle_error(response)
        return response.json()

    result = await _call(access_token)
    if result is None:
        return await _refresh_and_retry(_call)
    return result


async def list_api_key_entries(access_token: str) -> list[dict]:
    """
    GET /apikeys — return all API key entries as lightweight list items.
    Returns names and service hints only — no encrypted blob values.
    """
    async def _call(token: str):
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{BASE_URL}/apikeys",
                headers=_auth_headers(token),
                timeout=30.0,
            )
        if response.status_code == 401:
            return None
        _handle_error(response)
        return response.json().get("entries", [])

    result = await _call(access_token)
    if result is None:
        return await _refresh_and_retry(_call)
    return result


class ApiKeyLookupError(RuntimeError):
    """A caller-supplied API key name did not resolve to one stored key."""


class AmbiguousApiKeyError(ApiKeyLookupError):
    """A bare name matches more than one stored key, so no single key may be chosen.

    Raised instead of guessing (and instead of the ``404 No API key entry found`` that a
    leaf-only lookup used to produce, which states the opposite of the truth).
    ``candidates`` holds every stored name that matched, in list order.
    """

    def __init__(self, name: str, candidates: list[str]):
        self.name = name
        self.candidates = list(candidates)
        joined = ", ".join(f"'{candidate}'" for candidate in self.candidates)
        super().__init__(
            f"API key name '{name}' is ambiguous — it matches {len(self.candidates)} stored "
            f"keys: {joined}. Pass the full stored name exactly as list_api_keys returns it."
        )


def _leaf(name: str) -> str:
    """The last '/'-separated segment of a stored name (the whole name when it is flat)."""
    return name.rsplit("/", 1)[-1]


async def resolve_api_key_name(access_token: str, name: str) -> str:
    """Resolve a caller-supplied API key name to the exact stored name.

    Two name shapes reach the API key tools:

    * a **full stored name** — what ``list_api_keys`` returns, e.g. ``project/.env/KEY`` or the
      unscoped ``env/.env/KEY`` (the shape ``scan_and_protect`` writes). It contains a slash and
      is passed through untouched: the backend route takes the whole string as one path
      parameter, so the slashes must reach the server as separators. Percent-encoding them
      (``%2F``) would send a name the route cannot match.
    * a **bare leaf** — a key's name on its own, e.g. ``KEY`` or the flat name of a standalone
      key such as ``pypi``. The stored name is the namespaced one, so a leaf is not addressable
      directly. It is resolved against ``GET /apikeys``: an exact stored name wins, otherwise a
      unique leaf match resolves to its stored name, several matches raise
      :class:`AmbiguousApiKeyError`, and no match raises :class:`ApiKeyLookupError`.

    Resolution happens BEFORE any single-key GET, so an ambiguous leaf can never become a 404 on
    the leaf that reads like the key does not exist.
    """
    if "/" in name:
        return name

    entries = await list_api_key_entries(access_token)
    stored = [entry["name"] for entry in entries if entry.get("name")]

    if name in stored:
        # A standalone key is addressed by its own name.
        return name

    matches = [stored_name for stored_name in stored if _leaf(stored_name) == name]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise AmbiguousApiKeyError(name, matches)

    raise ApiKeyLookupError(
        f"API key entry '{name}' not found. "
        "Run list_api_keys to see the exact stored names."
    )


async def get_api_key_entry(access_token: str, name: str) -> dict:
    """
    GET /apikeys/{name} — return the encrypted blob and iv for an API key.
    The MCP server decrypts this locally before using it.

    ``name`` may be the exact stored name (as returned by ``list_api_keys``) or a bare leaf; it
    is resolved first (see :func:`resolve_api_key_name`). The resolved name is sent as a single
    path parameter with its slashes intact.
    """
    resolved = await resolve_api_key_name(access_token, name)

    async def _call(token: str):
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{BASE_URL}/apikeys/{resolved}",
                headers=_auth_headers(token),
                timeout=30.0,
            )
        if response.status_code == 401:
            return None
        _handle_error(response)
        return response.json()

    result = await _call(access_token)
    if result is None:
        return await _refresh_and_retry(_call)
    return result


async def add_api_key_entry(
    access_token: str,
    name: str,
    service_hint: str,
    encrypted_blob: str,
    iv: str,
) -> dict:
    """
    POST /apikeys — store a new encrypted API key entry.

    The encrypted_blob and iv must be pre-encrypted locally using the VEK.
    The server never sees the plaintext key value.
    """
    async def _call(token: str):
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{BASE_URL}/apikeys",
                headers=_auth_headers(token),
                json={
                    "name": name,
                    "service_hint": service_hint,
                    "encrypted_blob": encrypted_blob,
                    "iv": iv,
                },
                timeout=30.0,
            )
        if response.status_code == 401:
            return None
        _handle_error(response)
        return response.json()

    result = await _call(access_token)
    if result is None:
        return await _refresh_and_retry(_call)
    return result


async def check_site_exists(access_token: str, site_name: str) -> dict:
    """
    GET /vault/proxy/check/{site_name} — verify a credential is stored.
    Returns exists bool and username_hint — never the password.
    """
    async def _call(token: str):
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{BASE_URL}/vault/proxy/check/{site_name}",
                headers=_auth_headers(token),
                timeout=30.0,
            )
        if response.status_code == 401:
            return None
        _handle_error(response)
        return response.json()

    result = await _call(access_token)
    if result is None:
        return await _refresh_and_retry(_call)
    return result


async def update_vault_entry_url(
    access_token: str,
    site_name: str,
    login_url: str,
) -> dict:
    """
    PUT /vault/{site_name} — update only the login_url field.
    Used by browser_login to persist auto-discovered login page URLs.
    """
    async def _call(token: str):
        async with httpx.AsyncClient() as client:
            response = await client.put(
                f"{BASE_URL}/vault/{site_name}",
                headers=_auth_headers(token),
                json={"login_url": login_url},
                timeout=30.0,
            )
        if response.status_code == 401:
            return None
        _handle_error(response)
        return response.json()

    result = await _call(access_token)
    if result is None:
        return await _refresh_and_retry(_call)
    return result


