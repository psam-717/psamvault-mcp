"""Where a stored secret may be sent.

A site password may go to that entry's own site: the registrable domain of its
name or login URL, and subdomains of that domain. An API key may go to hosts
the server can already tie to the key — a bundled verify recipe
(``mcp_server.verify_recipes``) matched from the service, the service hint, or
the key name, or a hostname written in those fields — and to the registrable
domain of each such host. Anything else is refused until a human allows it.

The allow-list is the per-entry ``allow_hosts`` list in ``~/.psamvault/policy.json``,
the file the psamvault CLI already uses for reveal policy (plan step 8,
``allow_hosts``). Shape::

    {
      "reveal": "human-only",
      "entries": {
        "ledger-key": {"allow_hosts": ["api.ledger.example"]},
        "github.com": {"allow_hosts": ["*.ghcr.io"]}
      }
    }

``entries`` is the per-entry object from that plan (``allow_hosts`` today;
``allow_commands`` and ``allow_inject`` are not enforced here). A leading
``*.`` allows that domain and its subdomains. Any other value is one host.
This module only reads the file. No MCP tool writes it, and no tool argument
grants a host.

An API key with no recipe and no hostname stays refused until a human adds a
host. A missing file allows nothing extra. A malformed file allows nothing
extra and the refusal says so. ``allow_hosts`` does not override the SSRF
block; callers check that first.

The CLI's ``policy.py`` does not parse ``entries`` yet. A human edits the JSON
directly. Teaching the CLI to validate the key, and a terminal command that
writes it, is follow-up work in psamvault-cli.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from mcp_server import verify_recipes
from mcp_server.public_suffix import registrable_domain

_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>]+", re.I)


def policy_path() -> Path:
    """The CLI policy file. Resolved at call time so tests can redirect home."""
    return Path.home() / ".psamvault" / "policy.json"


@dataclass
class LoadedPolicy:
    """Explicit allow-lists read from policy.json. Warnings are fail-closed."""

    entries: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def load_policy(path: Path | None = None) -> LoadedPolicy:
    """Read ``entries.*.allow_hosts``. Never raises. A bad file allows no host."""
    target = Path(path) if path is not None else policy_path()
    loaded = LoadedPolicy()
    try:
        if not target.exists():
            return loaded
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        loaded.warnings.append(f"could not read {target}: {exc}")
        return loaded
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        loaded.warnings.append(
            f"{target.name} is not valid JSON ({exc.msg}) — no hosts are allowed by policy"
        )
        return loaded
    if not isinstance(data, dict):
        loaded.warnings.append(f"{target.name} must contain a JSON object — no hosts are allowed by policy")
        return loaded

    entries = data.get("entries", {})
    if entries is None:
        return loaded
    if not isinstance(entries, dict):
        loaded.warnings.append("entries must be an object — ignored")
        return loaded

    for name, spec in entries.items():
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(spec, dict):
            loaded.warnings.append(f"entries[{name!r}] must be an object — ignored")
            continue
        hosts = spec.get("allow_hosts", [])
        if hosts is None:
            continue
        if not isinstance(hosts, list):
            loaded.warnings.append(f"entries[{name!r}].allow_hosts must be a list — ignored")
            continue
        cleaned: list[str] = []
        for item in hosts:
            norm = normalize_allow_spec(str(item))
            if norm:
                cleaned.append(norm)
            elif str(item).strip():
                loaded.warnings.append(f"ignored allow_hosts value {item!r} for {name!r}")
        loaded.entries[name] = cleaned
    return loaded


def extract_host(value: str) -> str | None:
    """Hostname from a URL, a host, or ``host:port``. None when it is not one."""
    text = (value or "").strip()
    if not text or any(ch.isspace() for ch in text):
        return None
    if "://" in text or text.startswith("//"):
        parsed = urlparse(text if "://" in text else "https:" + text)
        host = parsed.hostname
    elif "/" in text:
        host = urlparse("https://" + text).hostname
    else:
        host = text
        if host.startswith("[") and "]" in host:
            host = host[1:host.index("]")]
        elif host.count(":") == 1 and not _is_ip(host):
            host = host.split(":", 1)[0]
    if not host:
        return None
    host = host.strip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if _is_ip(host) or _is_dns_host(host):
        return host
    return None


def normalize_allow_spec(value: str) -> str | None:
    """``*.example.com`` or one host. None when the value is not a host."""
    text = (value or "").strip().lower()
    if not text:
        return None
    wildcard = text.startswith("*.")
    body = text[2:] if wildcard else text
    host = extract_host(body)
    if not host:
        return None
    return f"*.{host}" if wildcard else host


def explicit_hosts(policy: LoadedPolicy, names: list[str]) -> list[str]:
    """Allow-list entries whose key is one of ``names`` or its leaf, case-insensitive."""
    wanted: set[str] = set()
    for name in names:
        if not name or not str(name).strip():
            continue
        text = str(name).strip()
        wanted.add(text.casefold())
        leaf = text.rsplit("/", 1)[-1].strip().casefold()
        if leaf:
            wanted.add(leaf)
    found: list[str] = []
    for key, hosts in policy.entries.items():
        if key.strip().casefold() in wanted:
            found.extend(hosts)
    return found


def site_hosts(candidates: list[str]) -> list[str]:
    """Hostnames implied by a vault entry's name and login URL."""
    found: list[str] = []
    for candidate in candidates:
        host = extract_host(str(candidate)) if candidate else None
        if host and host not in found:
            found.append(host)
    return found


def service_anchor_hosts(identifiers: list[str]) -> list[str]:
    """Hosts a key's service, hint, or name already point at.

    A bundled verify-recipe provider matched as the whole string or as a
    token (``GitHub API`` selects ``github``) contributes that recipe's host.
    A hostname or URL written in the same text contributes its host.
    """
    found: list[str] = []

    def add(host: str | None) -> None:
        if host and host not in found:
            found.append(host)

    for raw in identifiers:
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        for match in _URL_IN_TEXT.finditer(text):
            add(extract_host(match.group(0)))
        add(extract_host(text))
        for candidate in [text, *re.split(r"[^A-Za-z0-9]+", text)]:
            if len(candidate) < 3:
                continue
            recipe = verify_recipes.get_verify_recipe(candidate)
            if not recipe:
                continue
            add(urlparse(recipe["url"]).hostname)
    return found


def host_covered_by(anchor: str, target: str) -> bool:
    """True when ``target`` is ``anchor``'s registrable domain or a subdomain of it.

    An IP anchor matches only that IP. A public suffix with no registrable
    domain (``github.io`` itself) matches only that exact host.
    """
    anchor_host = extract_host(anchor) or anchor.strip(".").lower()
    target_host = extract_host(target) or target.strip(".").lower()
    if not anchor_host or not target_host:
        return False
    if _is_ip(anchor_host):
        return target_host == anchor_host
    reg = registrable_domain(anchor_host)
    if reg is None:
        return target_host == anchor_host
    return target_host == reg or target_host.endswith("." + reg)


def _explicit_match(target: str, specs: list[str]) -> bool:
    for spec in specs:
        if spec.startswith("*."):
            base = spec[2:]
            if target == base or target.endswith("." + base):
                return True
        elif target == spec:
            return True
    return False


def _how_to_allow(entry: str, host: str, policy: LoadedPolicy) -> str:
    note = ""
    if policy.warnings:
        note = " Policy file was not fully applied: " + "; ".join(policy.warnings) + "."
    return (
        f"A human can allow {host!r} by adding it to ~/.psamvault/policy.json "
        f"under entries[{entry!r}].allow_hosts and saving the file (mode 0600). "
        f"There is no MCP tool that grants a host. "
        f'Example: {{"entries": {{"{entry}": {{"allow_hosts": ["{host}"]}}}}}}.'
        + note
    )


def _roots(hosts: list[str]) -> str:
    roots: list[str] = []
    for host in hosts:
        reg = registrable_domain(host) or host
        if reg not in roots:
            roots.append(reg)
    return ", ".join(roots)


def refusal(
    url: str,
    *,
    entry_type: str,
    entry_names: list[str],
    site_candidates: list[str],
    service_identifiers: list[str],
    policy: LoadedPolicy | None = None,
) -> str | None:
    """Error string when ``url`` may not receive this secret. None when it may.

    Does not apply the SSRF block. Callers reject loopback and private targets
    first; an allow_hosts entry cannot turn that off.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "target_url must be an absolute http or https URL."
    target = parsed.hostname
    if target:
        target = target.strip(".").lower()
        try:
            target = target.encode("idna").decode("ascii")
        except UnicodeError:
            target = None
    if not target:
        return "target_url must be an absolute http or https URL with a hostname."

    loaded = policy if policy is not None else load_policy()
    names = [name for name in entry_names if name]
    entry = names[0] if names else ""
    if _explicit_match(target, explicit_hosts(loaded, names)):
        return None

    if entry_type == "vault":
        anchors = site_hosts(site_candidates)
        if any(host_covered_by(anchor, target) for anchor in anchors):
            return None
        if not anchors:
            why = (
                f"Refused to send the site password for {entry!r} to {target!r}. "
                "This entry has no hostname (its name is not a site and it has no login URL), "
                "so nothing is allowed until a human adds one. "
            )
        else:
            why = (
                f"Refused to send the site password for {entry!r} to {target!r}. "
                f"A site password may only be sent to its own site ({_roots(anchors)} and its subdomains). "
            )
        return why + _how_to_allow(entry, target, loaded)

    anchors = service_anchor_hosts(service_identifiers)
    if any(host_covered_by(anchor, target) for anchor in anchors):
        return None
    if not anchors:
        why = (
            f"Refused to send API key {entry!r} to {target!r}. "
            "This key has no known service host (no bundled verify recipe matches its name or service, "
            "and its service hint is not a hostname). "
            "It stays refused until a human allows a host. "
        )
    else:
        why = (
            f"Refused to send API key {entry!r} to {target!r}. "
            f"This key may only be sent to its service hosts ({_roots(anchors)}, "
            "including each registrable domain and its subdomains) "
            "or to a host a human has allowed. "
        )
    return why + _how_to_allow(entry, target, loaded)


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _is_dns_host(host: str) -> bool:
    if not host or len(host) > 253 or "." not in host:
        return False
    return all(_HOST_LABEL.match(label) for label in host.split("."))
