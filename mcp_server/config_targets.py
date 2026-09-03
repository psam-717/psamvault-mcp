"""Config-target adapters for psamvault-mcp.

Each supported agent host gets an adapter that knows where its MCP config
lives and how to add/replace one `mcp_servers.<name>` entry without
disturbing anything else in the file.

v1 target: Hermes (comment-rich YAML, `config.yaml`). The write is a
comment-preserving ruamel round-trip so hand-maintained configs survive.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ruamel.yaml import YAML

from mcp_server.log import get_logger

logger = get_logger()


@dataclass
class ServerSpec:
    """One MCP server entry to write into a target agent config.

    Exactly one transport shape is expected: HTTP (``url`` + optional
    ``headers``) or stdio (``command`` + optional ``args``/``env``).
    """

    name: str
    url: str | None = None
    headers: dict[str, str] | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None

    def validate(self) -> None:
        """Raise ValueError unless exactly one transport shape is set."""
        has_http = self.url is not None
        has_stdio = self.command is not None
        if has_http == has_stdio:
            raise ValueError(
                "provide exactly one transport: either url (HTTP) or command (stdio)"
            )


def _entry_dict(spec: ServerSpec) -> dict[str, Any]:
    """Build the mcp_servers.<name> value for the target config."""
    entry: dict[str, Any] = {}
    if spec.url is not None:
        entry["url"] = spec.url
    if spec.headers:
        entry["headers"] = dict(spec.headers)
    if spec.command is not None:
        entry["command"] = spec.command
    if spec.args:
        entry["args"] = list(spec.args)
    if spec.env:
        entry["env"] = dict(spec.env)
    return entry


def resolve_hermes_config_path(
    env: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the Hermes ``config.yaml`` path.

    Priority: ``HERMES_HOME`` env var → Windows ``%LOCALAPPDATA%/hermes`` →
    ``~/.hermes`` (Unix/macOS). Pass ``env`` explicitly in tests.
    """
    if env is None:
        env = os.environ
    home = env.get("HERMES_HOME")
    if home:
        return Path(home) / "config.yaml"
    if os.name == "nt":
        local = env.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "hermes" / "config.yaml"
    return Path.home() / ".hermes" / "config.yaml"


def write_hermes_mcp_server(
    config_path: Path,
    spec: ServerSpec,
    dry_run: bool = False,
    replace: bool = False,
) -> dict[str, Any]:
    """Add or replace ``mcp_servers.<spec.name>`` in a Hermes config.yaml.

    With ``dry_run=True`` the change is computed but nothing is written.
    Returns ``{"action": "added"|"replaced", "config_path": str,
    "backup_path": str|None, "server_name": str}`` (backup_path is None on
    dry-run). Raises ``ValueError`` if the entry already exists (and
    ``replace`` is False) or the config has no ``mcp_servers`` mapping.
    """
    spec.validate()

    text = config_path.read_text(encoding="utf-8")
    yaml = YAML()
    yaml.preserve_quotes = True
    data = yaml.load(text)

    servers = data.get("mcp_servers")
    if servers is None:
        # Fresh/minimal config — create the section so the entry can be added.
        servers = data.setdefault("mcp_servers", {})
    if not isinstance(servers, dict):
        raise ValueError("'mcp_servers' is not a mapping")

    action = "added"
    if spec.name in servers:
        if not replace:
            raise ValueError(
                f"mcp_servers entry '{spec.name}' already exists (pass replace=true to overwrite)"
            )
        action = "replaced"

    servers[spec.name] = _entry_dict(spec)

    if dry_run:
        return {
            "action": action,
            "config_path": str(config_path),
            "backup_path": None,
            "server_name": spec.name,
        }

    # Timestamped backup BEFORE any write so a bad edit is always recoverable.
    backup_path = config_path.with_name(
        f"{config_path.name}.bak-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    )
    backup_path.write_text(text, encoding="utf-8")

    with config_path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)

    logger.info(
        "config_targets: %s mcp_servers.%s in %s (backup %s)",
        action,
        spec.name,
        config_path,
        backup_path.name,
    )
    return {
        "action": action,
        "config_path": str(config_path),
        "backup_path": str(backup_path),
        "server_name": spec.name,
    }
