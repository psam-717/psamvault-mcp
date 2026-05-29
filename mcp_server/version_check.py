"""
Startup version check for psamvault-mcp.

Checks PyPI for a newer release once per server start and prints a notice
to stderr if one is available. Suppresses repeat notices for the same
latest version by persisting the last-seen version to ~/.psamvault/last_seen_version
(shared with the CLI).

All errors are silently swallowed so a network failure never prevents the
server from starting.
"""

import importlib.metadata
from pathlib import Path

import httpx


PYPI_URL = "https://pypi.org/pypi/psamvault-mcp/json"
_VERSION_FILE = Path.home() / ".psamvault" / "last_seen_version"


def version_tuple(v: str) -> tuple[int, ...]:
    """Convert a version string like '1.2.3' into a comparable tuple."""
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except ValueError:
        return (0,)


def _get_installed_version() -> str | None:
    try:
        return importlib.metadata.version("psamvault-mcp")
    except importlib.metadata.PackageNotFoundError:
        return None


def _get_latest_version() -> str | None:
    try:
        response = httpx.get(PYPI_URL, timeout=3)
        response.raise_for_status()
        return response.json()["info"]["version"]
    except Exception:
        return None


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

    if version_tuple(latest) <= version_tuple(installed):
        return

    # Suppress if we already notified for this latest version
    last_seen = _get_last_seen_version()
    if last_seen == latest:
        return

    _set_last_seen_version(latest)
    _print_update_notice(installed, latest)


def _print_update_notice(installed: str, latest: str) -> None:
    """Print the update notification to stderr."""
    from mcp_server.log import get_logger

    logger = get_logger()
    logger.info("Update available: %s -> %s", installed, latest)
    logger.info("Run  pipx upgrade psamvault-mcp  to update.")