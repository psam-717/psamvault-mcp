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

def _decrypt_site_credential(site_name: str) -> dict:
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
    entry = api_client.get_vault_entry(access_token, site_name)
    
    vek = get_vek()
    
    return decrypt_credentials(
        vek=vek,
        encrypted_blob=entry["encrypted_blob"],
        iv=entry["iv"]
    )
    
    

# Tool functions
def list_vault_sites() -> dict:
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
        entries = api_client.list_vault_entries(access_token)
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
    
    

def check_credential_exists(site_name: str) -> dict:
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
        return api_client.check_site_exists(access_token, site_name)
    except Exception as e:
        return  {"error": str(e)}
    

def use_credential(
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
        credentials = _decrypt_site_credential(site_name)
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
        result = api_client.proxy_request(
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
    

def get_username_for_site(site_name: str) -> dict:
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
        credentials = _decrypt_site_credential(site_name)
        
        return {
            "site_name": site_name,
            "username": credentials["username"]
        }
    except RuntimeError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}