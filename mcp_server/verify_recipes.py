"""Bundled verify-recipe store for export_key_to_mcp_config / verify_api_key.

A recipe describes how to prove a vault API key is valid for a provider:
which read-only endpoint to hit, with which method, and which HTTP status
means "key is good". Recipes are NON-SECRET transport metadata — never keys.

Schema (PLAN.md Decision 5 — shaped so the future MCP endpoint registry can
reuse it without rework):
    {url, method, expect, auth_kind}

Only providers whose recipes have been verified against the live service
belong here (render verified Sep 3 2026: GET /v1/owners -> 200).
"""

from __future__ import annotations

VERIFY_RECIPES: dict[str, dict] = {
    "render": {
        "url": "https://api.render.com/v1/owners",
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
