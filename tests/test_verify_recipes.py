"""Tests for the bundled verify-recipe store (mcp_server/verify_recipes.py).

Analysis:
- Unit under test: VERIFY_RECIPES registry + get_verify_recipe(provider) lookup.
- Inputs: provider name (str).
- Outputs: recipe dict {url, method, expect, auth_kind} or None for unknown.
- Happy paths: known provider (render) resolves a complete recipe.
- Edge cases: unknown provider -> None; case/whitespace normalization; the
  bundled table is always internally valid (every entry well-formed).
- Failure cases: recipe missing a required field is caught by validation;
  unknown providers never raise (lookup is safe).
- Mocked: nothing external (pure data module). Validation loops the real
  bundled table so a bad entry added later fails CI immediately.
- Shape note (Decision 5): recipes are provider-keyed and use fields
  (url/method/expect/auth_kind) the future MCP endpoint registry can reuse.
"""

import sys as _sys
import os as _os

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import pytest

# FAILING — mcp_server.verify_recipes not yet written
from mcp_server import verify_recipes

REQUIRED_FIELDS = {"url", "method", "expect", "auth_kind"}


def test_render_recipe_known_provider__returns_complete_http_recipe():
    recipe = verify_recipes.get_verify_recipe("render")
    assert recipe is not None
    assert recipe["url"] == "https://api.render.com/v1/owners"
    assert recipe["method"] == "GET"
    assert recipe["expect"] == 200
    assert recipe["auth_kind"] == "bearer"


def test_openrouter_recipe_known_provider__returns_complete_http_recipe():
    recipe = verify_recipes.get_verify_recipe("openrouter")
    assert recipe is not None
    assert recipe["url"] == "https://openrouter.ai/api/v1/auth/key"
    assert recipe["method"] == "GET"
    assert recipe["expect"] == 200
    assert recipe["auth_kind"] == "bearer"


def test_tavily_recipe_known_provider__returns_complete_http_recipe():
    # verified live Sep 11 2026: GET /usage -> 200 with a valid bearer key
    recipe = verify_recipes.get_verify_recipe("tavily")
    assert recipe is not None
    assert recipe["url"] == "https://api.tavily.com/usage"
    assert recipe["method"] == "GET"
    assert recipe["expect"] == 200
    assert recipe["auth_kind"] == "bearer"


def test_github_recipe_known_provider__returns_complete_http_recipe():
    # verified live Sep 11 2026: GET /user -> 200 (login psam-717) with a bearer token
    recipe = verify_recipes.get_verify_recipe("github")
    assert recipe is not None
    assert recipe["url"] == "https://api.github.com/user"
    assert recipe["method"] == "GET"
    assert recipe["expect"] == 200
    assert recipe["auth_kind"] == "bearer"


def test_service_hint_casing_matches_recipes():
    # Vault service hints are free text ("Tavily", "Github"), so lookup normalises.
    for hint, provider in (
        ("Tavily", "tavily"),
        ("Github", "github"),
        ("Render", "render"),
        ("openrouter", "openrouter"),
    ):
        assert verify_recipes.get_verify_recipe(hint) == verify_recipes.get_verify_recipe(provider)


def test_providers_without_a_read_only_whoami_stay_absent():
    # A PyPI upload token cannot be validated read-only (the public JSON API answers for anyone), so a
    # recipe would silently "verify" an invalid key. Absence is the correct state.
    known = verify_recipes.known_providers()
    assert "pypi" not in known
    assert "testpypi" not in known


def test_lookup_unknown_provider__returns_none():
    assert verify_recipes.get_verify_recipe("definitely-not-a-provider") is None


def test_lookup_case_and_whitespace__normalized_to_lowercase_stripped():
    # Providers are matched case-insensitively and after trimming whitespace.
    assert verify_recipes.get_verify_recipe("  Render  ") == verify_recipes.get_verify_recipe("render")


def test_known_providers__sorted_list_and_includes_render_and_openrouter():
    known = verify_recipes.known_providers()
    assert known == sorted(known)
    assert "render" in known
    assert "openrouter" in known


def test_every_bundled_recipe__is_well_formed():
    # Guards the curated table itself: no entry may drift into a bad shape.
    for provider in verify_recipes.known_providers():
        recipe = verify_recipes.get_verify_recipe(provider)
        assert set(recipe) == REQUIRED_FIELDS, f"{provider} recipe fields wrong: {sorted(recipe)}"
        assert recipe["url"].startswith("https://"), f"{provider} url must be https"
        assert isinstance(recipe["expect"], int), f"{provider} expect must be an int status"
        assert recipe["auth_kind"] in {"bearer", "api_key_header", "basic_auth"}


def test_recipe_store__contains_no_secret_material():
    # Recipes are non-secret transport metadata; a leaked key in the table
    # would be a hard failure.
    import json

    blob = json.dumps(verify_recipes.get_verify_recipe("render")).lower()
    for marker in ("sk-", "rnd_", "api_key=", "secret"):
        assert marker not in blob
