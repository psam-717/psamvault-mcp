import os
import httpx

from mcp_server.session import load_config

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


def list_vault_entries(access_token: str) -> list[dict]:
    """
    GET /vault — return all vault entries as lightweight list items.
    Returns site names and username hints only — no credential values.
    """
    response = httpx.get(
        f"{BASE_URL}/vault",
        headers=_auth_headers(access_token),
        timeout=30.0
    )
    _handle_error(response)
    return response.json().get("entries", [])


def get_vault_entry(access_token: str, site_name: str) -> dict:
    """
    GET /vault/{site_name} — return the encrypted blob and iv for a site.
    The MCP server decrypts this locally before passing to the proxy.
    """
    response = httpx.get(
        f"{BASE_URL}/vault/{site_name}",
        headers=_auth_headers(access_token),
        timeout=30.0,
    )
    _handle_error(response)
    return response.json()
    
    
def check_site_exists(access_token: str, site_name: str) -> dict:
    """
    GET /vault/proxy/check/{site_name} — verify a credential is stored.
    Returns exists bool and username_hint — never the password.
    """
    response = httpx.get(
        f"{BASE_URL}/vault/proxy/check/{site_name}",
        headers=_auth_headers(access_token),
        timeout=30.0,
    )
    _handle_error(response)
    return response.json()


def proxy_request(
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
    
    # Embed credential fields for backend injection
    # These are stripped server-side before the outbound request
    request_body["_credential_username"] = credential_username
    request_body["_credential_password"] = credential_password
    
    response = httpx.post(
        f"{BASE_URL}/vault/proxy",
        headers=_auth_headers(access_token),
        json={
            "site_name": site_name,
            "target_url": target_url,
            "method": method,
            "inject_as": inject_as,
            "header_name": header_name,
            "body": request_body,
            "extra_headers": extra_headers,
        },
        timeout=60.0,
    )
    _handle_error(response)
    return response.json()
    
    
    
    