"""
env_scanner — .env file scanning and API key/secret detection module.

Detects exposed secrets in .env files by analysing key names and
value prefixes. Designed for integration with psamvault's protect
and migrate tooling — entries that already start with "psamvault:"
are flagged as already_protected and skipped during scans.

Typical usage:
    from mcp_server.env_scanner import scan_project

    result = scan_project("/path/to/project")
    print(f"Found {result['secrets_found']} secrets")
"""

import re
from pathlib import Path
from typing import Optional

# ── Pattern definitions ──────────────────────────────────────────────────────

# Layer 1: Key name patterns — matched against the variable name (uppercased)
KEY_NAME_PATTERNS: list[str] = [
    r".*_API_KEY$",
    r".*_SECRET$",
    r".*_TOKEN$",
    r".*_PASSWORD$",
    r".*_DATABASE_URL$",
    r".*_KEY$",  # catches SUPABASE_ANON_KEY etc. (lower confidence alone)
]

# Layer 2: Value prefix patterns — (regex, human-readable label)
VALUE_PREFIX_PATTERNS: list[tuple[str, str]] = [
    (r"^sk-", "OpenAI/Anthropic secret key"),
    (r"^ghp_[a-zA-Z0-9]{36}", "GitHub personal access token"),
    (r"^gho_[a-zA-Z0-9]{36}", "GitHub OAuth token"),
    (r"^ghu_[a-zA-Z0-9]{16}", "GitHub user token"),
    (r"^xox[bprsa]-", "Slack token"),
    (r"^xapp-", "Slack app token"),
    (r"^AKIA[0-9A-Z]{16}", "AWS access key"),
    (r"^pk_live_", "Stripe live publishable key"),
    (r"^sk_live_", "Stripe live secret key"),
    (r"^pk_test_", "Stripe test publishable key"),
    (r"^sk_test_", "Stripe test secret key"),
    (r"^ACI[0-9a-f]{32}", "Azure container instance"),
    (r"^eyJ", "JWT token"),
    (r"^-----BEGIN", "Private key"),
]

# ── Gitignore helpers ────────────────────────────────────────────────────────


def _load_gitignore(project_dir: Path) -> list[str]:
    """Load gitignore patterns from project root."""
    gitignore_path = project_dir / ".gitignore"
    if not gitignore_path.exists():
        return []
    patterns = []
    for line in gitignore_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def _is_ignored(path: Path, project_dir: Path, gitignore_patterns: list[str]) -> bool:
    """Check if a path matches any gitignore pattern.

    Handles:
    - Simple names: ``node_modules/``, ``*.log``
    - Relative paths: ``services/auth/.env``
    - Pattern parts: ``.env`` in a path component
    """
    try:
        rel = path.relative_to(project_dir)
    except ValueError:
        return False

    rel_str = str(rel).replace("\\", "/")  # Normalise to forward slashes

    for pattern in gitignore_patterns:
        if pattern.startswith(".env"):
            continue  # Don't ignore .env files themselves

        norm_pattern = pattern.replace("\\", "/").strip("/")

        # Relative path pattern (contains /): exact suffix match on the relative path
        if "/" in norm_pattern:
            if rel_str == norm_pattern or rel_str.endswith("/" + norm_pattern):
                return True
        # Directory-level pattern ending with /
        elif norm_pattern.endswith("/") and norm_pattern.rstrip("/") in rel.parts:
            return True
        # Simple glob pattern (*.log, etc.) — check the filename
        elif "*" in norm_pattern:
            import fnmatch
            if fnmatch.fnmatch(rel.name, norm_pattern):
                return True
        # Plain name: check if it appears in any part
        elif norm_pattern in rel.parts:
            return True

    return False


# ── File finding ─────────────────────────────────────────────────────────────


def find_env_files(project_dir: str) -> list[Path]:
    """Recursively find all ``.env*`` files in a project directory.

    - Expands ``~`` in the path.
    - Respects ``.gitignore`` — files ignored by git are excluded.
    - Skips the ``.git/`` directory entirely.
    - Skips files whose name contains ``example`` (e.g. ``.env.example``).

    Returns a list of :class:`Path` objects, sorted by path.
    """
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        return []

    gitignore_patterns = _load_gitignore(root)

    env_files: list[Path] = []

    for entry in root.rglob(".env*"):
        # Skip .git directory
        if ".git" in entry.parts:
            continue

        # Only files, not directories
        if not entry.is_file():
            continue

        # Skip files with "example" in the filename
        if "example" in entry.name.lower():
            continue

        # Respect .gitignore
        if _is_ignored(entry, root, gitignore_patterns):
            continue

        env_files.append(entry)

    env_files.sort()
    return env_files


# ── Entry scanning ──────────────────────────────────────────────────────────


def _matches_name_pattern(key: str) -> Optional[str]:
    """Return the matched pattern label if the key matches any name pattern.

    Returns ``None`` if no pattern matches.
    """
    upper_key = key.upper()
    for pattern in KEY_NAME_PATTERNS:
        if re.match(pattern, upper_key):
            return f"name_pattern ({pattern})"
    return None


def _matches_value_prefix(value: str) -> Optional[str]:
    """Return a human-readable label if the value matches any prefix pattern.

    Returns ``None`` if no pattern matches.
    """
    for pattern, label in VALUE_PREFIX_PATTERNS:
        if re.match(pattern, value):
            return f"value_prefix ({label})"
    return None


def scan_env_file(env_path: Path) -> list[dict]:
    """Parse a single ``.env`` file and detect secrets.

    Args:
        env_path: Path to a ``.env`` file.

    Returns:
        A list of detected secret entries. Each entry is a dict with keys:
            - ``key``: The environment variable name.
            - ``file``: The basename of the file.
            - ``value_preview``: First 5 characters + ``"..."``.
            - ``detected_by``: Description of what triggered detection.
            - ``confidence``: ``"certain"`` (both layers), ``"high"`` (one layer).
            - ``already_protected``: ``True`` if value starts with ``"psamvault:"``.
            - ``index``: Zero-based line index in the file for replacement.
    """
    if not env_path.is_file():
        return []

    results: list[dict] = []
    filename = env_path.name

    try:
        lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []

    for idx, line in enumerate(lines):
        stripped = line.strip()

        # Skip comments and blank lines
        if not stripped or stripped.startswith("#"):
            continue

        # Parse KEY=VALUE (allow optional export prefix)
        match = re.match(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if not match:
            continue

        key = match.group(1).strip()
        value = match.group(2).strip()

        # Strip surrounding quotes (single or double)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        # Also handle value=  (no value after equals)
        if not value:
            continue

        already_protected = value.startswith("psamvault:")

        # Run both detection layers
        name_match = _matches_name_pattern(key)
        value_match = _matches_value_prefix(value)

        # Determine confidence
        if name_match and value_match:
            confidence = "certain"
            detected_by = f"{name_match} + {value_match}"
        elif name_match:
            confidence = "high"
            detected_by = name_match
        elif value_match:
            confidence = "high"
            detected_by = value_match
        else:
            # Not a secret — skip
            continue

        # Build preview (first 5 chars + "...")
        preview = value[:5] + "..." if len(value) > 5 else value

        results.append({
            "key": key,
            "file": filename,
            "value_preview": preview,
            "detected_by": detected_by,
            "confidence": confidence,
            "already_protected": already_protected,
            "index": idx,
        })

    return results


# ── High-level orchestrator ─────────────────────────────────────────────────


def scan_project(project_dir: str) -> dict:
    """Scan a project directory for exposed secrets in ``.env`` files.

    Combines :func:`find_env_files` and :func:`scan_env_file` into a single
    high-level operation.

    Args:
        project_dir: Path to the project directory (``~`` expansion supported).

    Returns:
        A dict with:
            - ``scanned_dir``: The resolved project directory path.
            - ``files_scanned``: List of ``.env`` file paths found.
            - ``secrets_found``: Count of secrets found (excluding already-protected).
            - ``already_protected``: Count of already-protected entries.
            - ``candidates``: List of detected secret entries (with ``already_protected``
              filtered out if ``True``).
            - ``files_not_gitignored``: List of ``.env`` files that should be in
              ``.gitignore`` but aren't.
    """
    root = Path(project_dir).expanduser().resolve()

    env_files = find_env_files(str(root))

    gitignore_patterns = _load_gitignore(root)
    # Check which .env files are not in .gitignore
    files_not_gitignored: list[str] = []
    for env_file in env_files:
        is_gitignored = _is_ignored(env_file, root, gitignore_patterns)
        if not is_gitignored:
            # Check if there's a generic .env pattern in gitignore
            has_env_pattern = any(
                p == ".env" or p.startswith(".env") for p in gitignore_patterns
            )
            if not has_env_pattern:
                files_not_gitignored.append(str(env_file))

    all_candidates: list[dict] = []
    already_protected_count = 0
    secrets_found = 0

    for env_file in env_files:
        entries = scan_env_file(env_file)
        for entry in entries:
            if entry["already_protected"]:
                already_protected_count += 1
            else:
                secrets_found += 1
            all_candidates.append(entry)

    return {
        "scanned_dir": str(root),
        "files_scanned": [str(p) for p in env_files],
        "secrets_found": secrets_found,
        "already_protected": already_protected_count,
        "candidates": all_candidates,
        "files_not_gitignored": files_not_gitignored,
    }
