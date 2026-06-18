"""
stripe_capture — Capture credentials provisioned by Stripe Projects.

After an agent runs ``stripe projects add <provider>``, the provisioned
credentials land in the project's ``.env`` file as plaintext. This module
provides the tools to:

1. Run ``stripe projects env --pull`` to sync fresh credentials.
2. Parse ``.env`` for secrets using the same detection engine as
   :mod:`mcp_server.env_scanner`.
3. Encrypt each secret with the VEK and store it in the psamvault API key
   store under a name like ``stripe/<provider>/<KEY_NAME>``.
4. Replace the plaintext value with a ``psamvault:<KEY_NAME>`` placeholder.

Typical usage (via the MCP tool)::

    result = await capture_stripe_credentials(
        provider="neon",
        project_dir="/path/to/project",
    )
"""

import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Optional

from mcp_server.crypto import encrypt_api_key

__all__ = ["capture_stripe_credentials", "get_env_file_path"]


# ── Provider-agnostic key name patterns ───────────────────────────────────────
# These match the same key names env_scanner uses, plus Stripe-specific ones
# that contain "STRIPE" or "PROJECT".

STRIPE_SPECIFIC_PATTERNS: list[str] = [
    r".*_PROJECT_.*",           # STRIPE_PROJECT_NAME, etc.
    r".*_STRIPE_.*",            # STRIPE_SECRET_KEY, STRIPE_PUBLISHABLE_KEY
    r".*_ENV_\w+$",             # NEON_ENV_VARIABLES (sometimes includes env info)
]


def _matches_stripe_or_known(key: str) -> bool:
    """Check if a key name matches a known secret pattern (reuses env_scanner patterns).

    Also catches Stripe-specific env vars that aren't secrets but are
    provisioning metadata — these are left untouched.
    """
    # First, check the known patterns from env_scanner
    from mcp_server.env_scanner import KEY_NAME_PATTERNS

    upper_key = key.upper()
    for pattern in KEY_NAME_PATTERNS:
        if re.match(pattern, upper_key):
            return True
    for pattern in STRIPE_SPECIFIC_PATTERNS:
        if re.match(pattern, upper_key):
            # Stripe-specific patterns indicate project metadata, not secrets.
            # Return False so these get skipped.
            return False
    return False


def _is_known_non_secret(key: str) -> bool:
    """Return True if the key is a known non-secret that should never be captured."""
    upper = key.upper()
    NON_SECRETS = {
        "NODE_ENV",
        "NODE_VERSION",
        "PYTHON_VERSION",
        "PORT",
        "HOST",
        "HOSTNAME",
        "DEBUG",
        "LOG_LEVEL",
        "ENVIRONMENT",
        "APP_ENV",
        "CI",
        "STRIPE_PROJECT_NAME",
        "STRIPE_ACCOUNT_ID",
        "PROJECT_NAME",
        "PROJECT_ID",
        "REGION",
        "DATABASE_URL",  # *_DATABASE_URL is already caught by env_scanner patterns
        "REDIS_URL",
        "REDIS_PORT",
    }
    return upper in NON_SECRETS


def _parse_env_file(env_path: Path) -> list[dict]:
    """Parse a .env file and return all key=value pairs.

    Returns list of dicts: {key, value, index, file}
    Uses the same parsing logic as env_scanner.scan_env_file but returns
    ALL non-comment, non-blank entries (not just secrets).
    """
    if not env_path.is_file():
        return []

    filename = env_path.name
    results: list[dict] = []

    try:
        lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        match = re.match(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if not match:
            continue

        key = match.group(1).strip()
        value = match.group(2).strip()

        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]

        if not value:
            continue

        results.append({
            "key": key,
            "value": value,
            "file": filename,
            "index": idx,
        })

    return results


async def _run_stripe_pull(project_dir: str) -> dict:
    """Run ``stripe projects env --pull`` and return the result.

    Returns a dict with:
        - ``success``: bool
        - ``output``: str (stdout if success, stderr if failure)
        - ``error``: str | None
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            shutil.which("stripe") or "stripe",
            "projects", "env", "--pull",
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)

        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace").strip()
            return {
                "success": False,
                "output": error_msg,
                "error": f"stripe projects env --pull exited with code {proc.returncode}: {error_msg}",
            }

        return {
            "success": True,
            "output": stdout.decode("utf-8", errors="replace").strip(),
            "error": None,
        }

    except FileNotFoundError:
        return {
            "success": False,
            "output": "",
            "error": (
                "Stripe CLI not found. Install it from https://stripe.com/docs/stripe-cli "
                "and authenticate with 'stripe login' or 'stripe projects auth'."
            ),
        }
    except asyncio.TimeoutError:
        return {
            "success": False,
            "output": "",
            "error": "stripe projects env --pull timed out after 30 seconds.",
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": f"Failed to run stripe CLI: {e}",
        }


def get_env_file_path(project_dir: str) -> Optional[Path]:
    """Locate the project's ``.env`` file.

    Returns the first found among (in priority order):
    1. ``.env`` in the project root
    2. ``.env.local`` in the project root
    3. Any ``.env*`` file in the project root (excluding examples)

    Returns ``None`` if no ``.env`` file is found.
    """
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        return None

    # Priority order
    candidates = [
        root / ".env",
        root / ".env.local",
    ]

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    # Fallback: find any .env* file (skip examples)
    for entry in sorted(root.glob(".env*")):
        if entry.is_file() and "example" not in entry.name.lower():
            return entry

    return None


async def capture_stripe_credentials(
    provider: str,
    project_dir: str | None = None,
    vek: bytes | None = None,
    access_token: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Capture credentials from Stripe Projects into psamvault.

    This is the core logic that:
    1. Runs ``stripe projects env --pull`` to sync fresh .env credentials.
    2. Parses .env for secrets using the env_scanner detection patterns.
    3. Encrypts each secret and stores it in the psamvault API key store.
    4. Replaces plaintext values with ``psamvault:<KEY_NAME>`` placeholders.

    Args:
        provider:       The Stripe Projects provider name, e.g. ``"neon"``,
                        ``"supabase"``, ``"openrouter"``.
        project_dir:    Project directory (defaults to CWD).
        vek:            Vault Encryption Key (raw 32 bytes). If ``None``, the
                        function reads it from the session. Pass when calling
                        from a context that already has it.
        access_token:   psamvault access token. If ``None``, reads from session.
        dry_run:        If ``True``, only detect and preview — don't encrypt
                        or store anything.

    Returns:
        A dict with keys:
        - ``success``: bool
        - ``provider``: str
        - ``project_dir``: str
        - ``env_file``: str | None — the .env file that was processed
        - ``captured``: list[dict] — each captured credential
        - ``captured_count``: int
        - ``files_modified``: list[str] — .env files that were modified
        - ``errors``: list[dict] | None — any per-key errors
        - ``message``: str — human-readable summary
        - ``stripe_output``: str | None — raw stripe CLI output
        - ``dry_run``: bool
    """
    if project_dir is None:
        project_dir = str(Path.cwd())

    # Step 1: Run stripe projects env --pull
    pull_result = await _run_stripe_pull(project_dir)
    stripe_output = pull_result.get("output")

    if not pull_result["success"]:
        return {
            "success": False,
            "provider": provider,
            "project_dir": project_dir,
            "env_file": None,
            "captured": [],
            "captured_count": 0,
            "files_modified": [],
            "errors": [{"error": pull_result["error"]}],
            "message": f"Failed to pull credentials from Stripe Projects: {pull_result['error']}",
            "stripe_output": stripe_output,
            "dry_run": dry_run,
        }

    # Step 2: Find the .env file
    env_path = get_env_file_path(project_dir)
    if env_path is None:
        return {
            "success": False,
            "provider": provider,
            "project_dir": project_dir,
            "env_file": None,
            "captured": [],
            "captured_count": 0,
            "files_modified": [],
            "errors": [],
            "message": (
                "No .env file found after pulling credentials. "
                "Stripe Projects may not be initialised in this directory. "
                f"Run 'stripe projects use' first."
            ),
            "stripe_output": stripe_output,
            "dry_run": dry_run,
        }

    # Step 3: Parse .env and detect secrets
    all_entries = _parse_env_file(env_path)
    secrets_found = []

    for entry in all_entries:
        key = entry["key"]
        value = entry["value"]

        # Skip already-protected entries
        if value.startswith("psamvault:"):
            continue

        # Skip known non-secrets
        if _is_known_non_secret(key):
            continue

        # Check if it matches secret patterns
        if _matches_stripe_or_known(key):
            secrets_found.append(entry)

    if not secrets_found:
        return {
            "success": True,
            "provider": provider,
            "project_dir": project_dir,
            "env_file": str(env_path),
            "captured": [],
            "captured_count": 0,
            "files_modified": [],
            "errors": [],
            "message": "No secrets found in .env after pull. All clear!",
            "stripe_output": stripe_output,
            "dry_run": dry_run,
        }

    if dry_run:
        # Build preview
        captured_preview = []
        for entry in secrets_found:
            captured_preview.append({
                "key": entry["key"],
                "file": entry["file"],
                "value_preview": entry["value"][:5] + "..." if len(entry["value"]) > 5 else entry["value"],
                "will_store_as": f"stripe/{provider}/{entry['key']}",
            })
        return {
            "success": True,
            "provider": provider,
            "project_dir": project_dir,
            "env_file": str(env_path),
            "captured": captured_preview,
            "captured_count": len(captured_preview),
            "files_modified": [],
            "errors": [],
            "message": f"Dry run: {len(captured_preview)} secrets would be captured. Pass dry_run=False to actually store them.",
            "stripe_output": stripe_output,
            "dry_run": True,
        }

    # Step 4: Encrypt and store each secret
    from mcp_server.session import get_access_token as _get_token, get_vek as _get_vek
    from mcp_server import api_client as ac

    # Resolve VEK and access token
    resolved_vek = vek if vek is not None else _get_vek()
    resolved_token = access_token if access_token is not None else _get_token()

    captured = []
    errors = []
    files_modified = set()
    lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()

    for entry in secrets_found:
        try:
            key_name = f"stripe/{provider}/{entry['key']}"

            # Encrypt the value
            encrypted_blob, iv = encrypt_api_key(
                vek=resolved_vek,
                service=f"Stripe/{provider}",
                api_key=entry["value"],
                notes=f"Auto-captured from Stripe Projects ({provider}) at {entry['file']}",
            )

            # Store in vault
            await ac.add_api_key_entry(
                access_token=resolved_token,
                name=key_name,
                service_hint=f"Stripe/{provider}",
                encrypted_blob=encrypted_blob,
                iv=iv,
            )

            # Replace in .env file
            old_line = lines[entry["index"]]
            new_line = re.sub(
                r"(=)(\s*).*",
                lambda m: f"{m.group(1)}{m.group(2)}psamvault:{entry['key']}",
                old_line,
            )
            lines[entry["index"]] = new_line
            files_modified.add(entry["file"])

            captured.append({
                "key": entry["key"],
                "file": entry["file"],
                "stored_as": key_name,
            })

        except Exception as e:
            errors.append({
                "key": entry["key"],
                "file": entry["file"],
                "error": str(e),
            })

    # Write the modified .env file
    if files_modified:
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {
        "success": True if not errors else len(errors) >= len(secrets_found),
        "provider": provider,
        "project_dir": project_dir,
        "env_file": str(env_path),
        "captured": captured,
        "captured_count": len(captured),
        "files_modified": sorted(files_modified),
        "errors": errors if errors else None,
        "message": (
            f"Captured {len(captured)} secrets from Stripe/{provider} into psamvault. "
            f"Plaintext values replaced with psamvault: placeholders."
            + (f" {len(errors)} errors." if errors else "")
        ),
        "stripe_output": stripe_output,
        "dry_run": False,
    }
