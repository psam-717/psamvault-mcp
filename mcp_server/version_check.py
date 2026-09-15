"""
Startup version check for psamvault-mcp.

Checks PyPI for a newer release once per server start and prints a notice
to stderr if one is available. Suppresses repeat notices for the same
latest version by persisting the last-seen version to ~/.psamvault/last_seen_version
(shared with the CLI).

The same probe backs `psamvault-mcp compat --check`, so the two surfaces cannot
disagree, and version ordering lives in mcp_server.versions.

All errors are silently swallowed so a network failure never prevents the
server from starting.
"""

import importlib.metadata
from pathlib import Path

import httpx

from mcp_server.versions import is_newer, vkey


PYPI_URL = "https://pypi.org/pypi/psamvault-mcp/json"

#: A version check must never delay a server start or a CLI check for long.
DEFAULT_TIMEOUT = 5.0

_VERSION_FILE = Path.home() / ".psamvault" / "last_seen_version"


def version_tuple(v: str) -> tuple[int, ...]:
    """Comparable key for a version string (implementation: mcp_server.versions.vkey).

    Kept under this name because callers and tests already import it; the single comparator lives
    in mcp_server.versions so this module and compat can never disagree.
    """
    return vkey(v)


def latest_published(timeout: float = DEFAULT_TIMEOUT) -> str | None:
    """The newest version on PyPI, or None when it cannot be determined.

    The ONLY PyPI probe in the package — the startup notice and `compat --check` both call it.
    Never raises: an unreachable or malformed index is reported as None so callers degrade to
    "unknown" instead of failing.
    """
    try:
        response = httpx.get(PYPI_URL, timeout=timeout)
        response.raise_for_status()
        version = response.json()["info"]["version"]
        return str(version) if version else None
    except Exception:
        return None


def _get_installed_version() -> str | None:
    try:
        return importlib.metadata.version("psamvault-mcp")
    except importlib.metadata.PackageNotFoundError:
        return None


def _get_latest_version() -> str | None:
    """Internal seam kept for the existing tests; delegates to the public probe."""
    return latest_published()


def update_available(installed: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> str | None:
    """Return the published version when it is newer than the installed one, else None.

    Used by `compat --check` to report `update_available`. Returns None both when the install is
    current and when PyPI cannot be reached; callers that must tell those apart call
    latest_published() directly.
    """
    current = installed or _get_installed_version()
    if not current:
        return None
    latest = latest_published(timeout=timeout)
    if not latest:
        return None
    return latest if is_newer(latest, current) else None


def _get_last_seen_version() -> str | None:
    try:
        if _VERSION_FILE.exists():
            return _VERSION_FILE.read_text().strip() or None
    except Exception:
        pass
    return None


def _set_last_seen_version(version: str) -> None:
    try:
        _VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        _VERSION_FILE.write_text(version)
    except Exception:
        pass


def check_for_update(silent: bool = True) -> None:
    """
    Check PyPI for a newer version and print a notice to stderr if found.

    Args:
        silent: If True (default), only prints when an update is available
                and hasn't been notified yet. Set to False for debug output.
    """
    installed = _get_installed_version()
    if not installed:
        return

    latest = _get_latest_version()
    if not latest:
        return

    if not is_newer(latest, installed):
        return

    # Suppress if we already notified for this latest version
    last_seen = _get_last_seen_version()
    if last_seen == latest:
        return

    _set_last_seen_version(latest)
    _print_update_notice(installed, latest)


def _upgrade_command(latest: str) -> str:
    """The apply command the flag gate will actually accept, for a release newer than this contract.

    The gate refuses a target the installed contract has never seen unless ``--allow-breaking`` is
    present, and "installed == contract, PyPI is newer" is exactly that case — so recommending plain
    ``--apply --latest`` here would print a command that exits 2 every time it is copied.
    """
    newest_known = None
    try:
        data = json.loads(Path(__file__).with_name("compatibility.json").read_text(encoding="utf-8"))
        versions = [r.get("mcp") for r in (data.get("releases") or []) if r.get("mcp")]
        if versions:
            newest_known = max(versions, key=vkey)
    except Exception:  # noqa: BLE001 - advice only; never break the notice chain
        newest_known = None
    parts = ["psamvault-mcp", "compat", "--apply", "--latest"]
    if newest_known and is_newer(latest, newest_known):
        parts.append("--allow-breaking")
    return " ".join(parts)


def _print_update_notice(installed: str, latest: str) -> None:
    """Print the update notification to stderr."""
    from mcp_server.log import get_logger

    logger = get_logger()
    logger.info("Update available: %s -> %s", installed, latest)
    logger.info("Run  %s  to update.", _upgrade_command(latest))
    logger.info(
        "(`pipx upgrade psamvault-mcp` can fail on Windows while MCP servers are running; "
        "see `psamvault-mcp doctor`.)"
    )