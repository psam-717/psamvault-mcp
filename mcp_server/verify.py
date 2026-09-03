"""Shared HTTP key-verification executor for export_key_to_mcp_config.

verify_key_http proves a decrypted API key is valid by hitting a cheap,
read-only provider endpoint and comparing the HTTP status against the
recipe's expectation. Error taxonomy (PLAN.md Decision 4):

- 401/403          -> error_class "key_invalid"      (key rejected by provider)
- anything else    -> error_class "probe_unavailable" (wrong probe, 5xx, network)

The key value is never part of the returned dict — callers must treat the
result as untrusted-for-secrets and never embed raw exception text (it can
echo request details).
"""

from __future__ import annotations

import httpx

_DEFAULT_TIMEOUT = 15.0


def _auth_headers(key: str, auth_kind: str, header_name: str | None) -> dict[str, str]:
    if auth_kind == "bearer":
        return {"Authorization": f"Bearer {key}"}
    if auth_kind == "api_key_header":
        if not header_name:
            raise ValueError("header_name is required when auth_kind='api_key_header'")
        return {header_name: key}
    raise ValueError(f"auth_kind={auth_kind!r} is not supported for key verification")


async def verify_key_http(
    key: str,
    *,
    url: str,
    method: str = "GET",
    expect: int = 200,
    auth_kind: str = "bearer",
    header_name: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict:
    """Probe ``url`` with ``key`` and return a redacted verification result.

    Result shape: {success, status, error_class, detail, probe_url}.
    ``error_class`` is None on success, else "key_invalid" or
    "probe_unavailable". The key never appears in the result.
    """
    headers = _auth_headers(key, auth_kind, header_name)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, url, headers=headers)
    except httpx.RequestError as exc:
        # Use only the exception class name — the message can echo the key.
        return {
            "success": False,
            "status": None,
            "error_class": "probe_unavailable",
            "detail": f"network error ({type(exc).__name__})",
            "probe_url": url,
        }

    status = response.status_code
    if status in (401, 403):
        return {
            "success": False,
            "status": status,
            "error_class": "key_invalid",
            "detail": f"key rejected by provider (HTTP {status})",
            "probe_url": url,
        }
    if status == expect:
        return {
            "success": True,
            "status": status,
            "error_class": None,
            "detail": "ok",
            "probe_url": url,
        }
    return {
        "success": False,
        "status": status,
        "error_class": "probe_unavailable",
        "detail": f"unexpected HTTP {status} (expected {expect})",
        "probe_url": url,
    }
