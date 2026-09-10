"""Config-target adapters for psamvault-mcp.

Each supported agent host gets an adapter that knows where its MCP config
lives and how to add/replace one `mcp_servers.<name>` entry without
disturbing anything else in the file.

v1 target: Hermes (comment-rich YAML, `config.yaml`). The write is a
comment-preserving ruamel round-trip so hand-maintained configs survive.
"""

from __future__ import annotations

import contextlib
import os
import re
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

    # A target that does not exist yet (scratch config, first-time install) is a legitimate
    # destination: treat it as an empty config instead of failing on the read. The same goes for a
    # file that exists but is empty — yaml.load("") returns None, which used to blow up as
    # "'NoneType' object has no attribute 'get'".
    text = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    yaml = YAML()
    yaml.preserve_quotes = True
    data = yaml.load(text) if text.strip() else None
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("config root is not a mapping")

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

    # Timestamped backup BEFORE any write so a bad edit is always recoverable. Nothing to back up
    # when the target is new/empty — report None rather than leaving an empty .bak behind.
    backup_path: Path | None = None
    if text:
        backup_path = config_path.with_name(
            f"{config_path.name}.bak-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        )
        backup_path.write_text(text, encoding="utf-8")

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)

    logger.info(
        "config_targets: %s mcp_servers.%s in %s (backup %s)",
        action,
        spec.name,
        config_path,
        backup_path.name if backup_path else "none (new file)",
    )
    return {
        "action": action,
        "config_path": str(config_path),
        "backup_path": str(backup_path) if backup_path else None,
        "server_name": spec.name,
    }


# ── Agent-host .env targets ───────────────────────────────────────────────────
# Only hosts whose .env location is VERIFIED appear here: writing a secret into a guessed path is
# the worst failure mode this feature can have. An unknown host must pass env_path explicitly.
_VERIFIED_ENV_HOSTS: tuple[str, ...] = ("hermes",)

_ENV_VAR_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def resolve_agent_env_path(
    agent: str = "hermes",
    env_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the ``.env`` file an agent host reads its tool variables from.

    An explicit ``env_path`` always wins. Otherwise ``agent`` must be in the verified table;
    an unknown host raises ``ValueError`` instead of guessing a location.
    """
    if env_path:
        return Path(env_path)
    if env is None:
        env = os.environ
    key = (agent or "").strip().lower()
    if key == "hermes":
        home = env.get("HERMES_HOME")
        if home:
            return Path(home) / ".env"
        if os.name == "nt":
            local = env.get("LOCALAPPDATA")
            base = Path(local) if local else Path.home() / "AppData" / "Local"
            return base / "hermes" / ".env"
        return Path.home() / ".hermes" / ".env"
    known = ", ".join(sorted(_VERIFIED_ENV_HOSTS))
    raise ValueError(
        f"unknown agent '{agent}': no verified .env location for it (known: {known}). "
        "Pass env_path=<path to the .env file> to target another host."
    )


def _render_env_value(value: str) -> str:
    """Bare value when it is dotenv-safe, otherwise double-quoted with escapes."""
    if value == "" or re.search(r"[\s\"'#]", value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def write_env_var(
    env_path: Path,
    name: str,
    value: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Set ``name=value`` in a dotenv-style file.

    Updates the existing line in place when the variable is already there (idempotent re-runs, no
    duplicate keys — different dotenv loaders pick different winners), appends otherwise. A
    timestamped backup is written before any modifying write, so a hand-set value stays recoverable.
    """
    if not _ENV_VAR_NAME_RE.match(name or ""):
        raise ValueError(f"invalid environment variable name: {name!r}")
    if len(value.splitlines()) != 1:
        raise ValueError("environment variable values must be single-line")

    text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    rendered = f"{name}={_render_env_value(value)}"

    action = "appended"
    out: list[str] = []
    line_number: int | None = None
    for index, line in enumerate(text.splitlines(), start=1):
        match = _ENV_VAR_LINE_RE.match(line)
        if match and match.group(1) == name:
            out.append(rendered)
            action = "updated"
            line_number = index
            continue
        out.append(line)
    if action == "appended":
        line_number = len(out) + 1
        out.append(rendered)

    result: dict[str, Any] = {
        "action": action,
        "env_path": str(env_path),
        "variable": name,
        "line": line_number,
        "backup_path": None,
    }
    if dry_run:
        return result

    backup_path: Path | None = None
    if text:
        backup_path = env_path.with_name(
            f"{env_path.name}.bak-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        )
        backup_path.write_text(text, encoding="utf-8")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    if os.name != "nt":
        # dotenv files are plaintext by design; keep them owner-only where the OS supports it.
        with contextlib.suppress(OSError):
            os.chmod(env_path, 0o600)

    result["backup_path"] = str(backup_path) if backup_path else None
    logger.info(
        "config_targets: %s %s in %s (backup %s)",
        action,
        name,
        env_path,
        backup_path.name if backup_path else "none (new file)",
    )
    return result
