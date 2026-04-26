import os
import httpx

from mcp_server.session import load_config, get_refresh_token, update_tokens

load_config()

BASE_URL = os.getenv("PSAMVAULT_API_URL", "https://psam-vault-backend.onrender.com")

def _auth_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


def _handle_error(response: httpx.Response) -> None:
    if not response.is_success:
        try:
            detail = response.json().get("detail", response.text)
        except (ValueError, httpx.RequestError):
            detail = response.text
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
    new tokens to the session file, then retry the original request.
    Raises RuntimeError if the refresh itself fails (session truly expired).
    """
    try:
        refresh_token = get_refresh_token()
        new_access, new_refresh = await _refresh_access_token(refresh_token)
        update_tokens(new_access, new_refresh)
        return await retry_fn(new_access)
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


async def proxy_request(
    access_token: str,
    site_name: str,
    target_url: str,
    method: str,
    inject_as: str,
    header_name: str | None,
    body: dict | None,
    extra_headers: dict | None,
    credential_username: str,
    credential_password: str,
) -> dict:
    """
    POST /vault/proxy — make an authenticated request via the backend.

    The credential username and password are passed in the request body
    under underscore-prefixed keys so the backend can inject them without
    storing. They travel over TLS only and are stripped before the
    outbound call to the target.
    """
    request_body = body or {}
    request_body["_credential_username"] = credential_username
    request_body["_credential_password"] = credential_password

    payload = {
        "site_name": site_name,
        "target_url": target_url,
        "method": method,
        "inject_as": inject_as,
        "header_name": header_name,
        "body": request_body,
        "extra_headers": extra_headers,
    }

    async def _call(token: str):
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{BASE_URL}/vault/proxy",
                headers=_auth_headers(token),
                json=payload,
                timeout=60.0,
            )
        if response.status_code == 401:
            return None
        _handle_error(response)
        return response.json()

    result = await _call(access_token)
    if result is None:
        return await _refresh_and_retry(_call)
    return result
