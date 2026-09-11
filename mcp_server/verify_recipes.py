"""Bundled verify-recipe store for export_key_to_mcp_config / verify_api_key.

A recipe describes how to prove a vault API key is valid for a provider:
which read-only endpoint to hit, with which method, and which HTTP status
means "key is good". Recipes are NON-SECRET transport metadata — never keys.

Schema (PLAN.md Decision 5 — shaped so the future MCP endpoint registry can
reuse it without rework):
    {url, method, expect, auth_kind}

Only providers whose recipes have been verified against the live service
belong here, and each entry records the date it was proven:
  - render      verified Sep 3 2026:  GET /v1/owners             -> 200 (bearer)
  - openrouter  verified Sep 3 2026:  GET /api/v1/auth/key       -> 200 (bearer)
  - tavily      verified Sep 11 2026: GET /usage                 -> 200 (bearer)
  - github      verified Sep 11 2026: GET /user                  -> 200 (bearer)

Deliberately ABSENT — no read-only whoami exists, so these need
``verify_url`` or ``skip_verify=true``:
  - pypi / testpypi — an upload token cannot be validated without attempting
    an upload (the public JSON API answers for anyone); verification is not
    possible read-only, so the store must not pretend otherwise.

Adding a provider: find a read-only endpoint that returns 200 for a VALID key
and != 200 for a bad one, probe it with the real key, then record it with the
date. Never add an endpoint that answers 200 anonymously — it would "verify"
an invalid key.
"""

from __future__ import annotations

VERIFY_RECIPES: dict[str, dict] = {
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/auth/key",
        "method": "GET",
        "expect": 200,
        "auth_kind": "bearer",
    },
    "render": {
        "url": "https://api.render.com/v1/owners",
        "method": "GET",
        "expect": 200,
        "auth_kind": "bearer",
    },
    "tavily": {
        "url": "https://api.tavily.com/usage",
        "method": "GET",
        "expect": 200,
        "auth_kind": "bearer",
    },
    "github": {
        "url": "https://api.github.com/user",
        "method": "GET",
        "expect": 200,
        "auth_kind": "bearer",
    },
}


def get_verify_recipe(provider: str) -> dict | None:
    """Return the verify recipe for a provider, or None if unknown.

    Matching is case-insensitive and ignores surrounding whitespace.
    """
    key = (provider or "").strip().lower()
    return VERIFY_RECIPES.get(key)


def known_providers() -> list[str]:
    """Sorted list of providers that have a bundled verify recipe."""
    return sorted(VERIFY_RECIPES)
