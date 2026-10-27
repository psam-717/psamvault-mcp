"""Registrable domain (eTLD+1) from a vendored Public Suffix List snapshot.

The list is ``mcp_server/public_suffix_list.dat`` (Mozilla Public Suffix List,
snapshot noted in that file's header). Both the ICANN and PRIVATE sections are
used: a site stored as ``psam.github.io`` must not authorise ``other.github.io``.

Unknown suffixes fall through to the implicit ``*`` rule, so ``leakcheck.example``
(``.example`` is not in the list) still has registrable domain ``leakcheck.example``.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path

_LIST_PATH = Path(__file__).with_name("public_suffix_list.dat")


@lru_cache(maxsize=1)
def _rules() -> frozenset[str]:
    rules: set[str] = set()
    text = _LIST_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip().lower()
        if not line or line.startswith("//"):
            continue
        rules.add(line)
    return frozenset(rules)


def _labels(host: str) -> list[str] | None:
    host = host.strip().strip(".").lower()
    if not host or ".." in host:
        return None
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    labels = host.split(".")
    if any(not label for label in labels):
        return None
    return labels


def public_suffix(host: str) -> str | None:
    """The prevailing public suffix for ``host``, or None for an IP / empty host.

    A host that is itself a public suffix returns that suffix (``co.uk``,
    ``github.io``, ``bar.ck`` under the ``*.ck`` wildcard).
    """
    labels = _labels(host)
    if not labels:
        return None
    rules = _rules()
    for start in range(len(labels)):
        candidate = ".".join(labels[start:])
        if "!" + candidate in rules:
            # Exception: the public suffix is the rule minus its leftmost label.
            rest = labels[start + 1 :]
            return ".".join(rest) if rest else ""
        if candidate in rules:
            return candidate
        rest = ".".join(labels[start + 1 :])
        wildcard = "*." + rest if rest else "*"
        if wildcard in rules:
            return candidate
    return labels[-1]


def registrable_domain(host: str) -> str | None:
    """eTLD+1 for ``host``.

    None when ``host`` is an IP, a single label, or itself a public suffix
    (there is no label left of the suffix to bind a site to).
    """
    labels = _labels(host)
    if not labels:
        return None
    suffix = public_suffix(host)
    if suffix is None:
        return None
    suffix_labels = suffix.split(".") if suffix else []
    if len(labels) <= len(suffix_labels):
        return None
    return ".".join(labels[-(len(suffix_labels) + 1) :])
