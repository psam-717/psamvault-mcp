"""Scrub a secret out of text an agent is about to see.

This is defence in depth. It is not a boundary: a transformation this list
does not know about can still carry the value. Destination binding
(``mcp_server.egress``) is what stops the secret being sent.
"""

from __future__ import annotations

import base64
import re
from urllib.parse import quote, quote_plus

# Encoded forms shorter than this are skipped. They collide with ordinary
# output (base64 of a 1-character secret is four characters). The raw value
# is still removed at any non-empty length, which is what the runner already did.
_MIN_ENCODED = 8


def _add(found: list[str], seen: set[str], value: str, *, minimum: int) -> None:
    if not value or len(value) < minimum or value in seen:
        return
    seen.add(value)
    found.append(value)


def secret_forms(secret: str, *, username: str | None = None) -> list[str]:
    """Forms of ``secret`` that scrubbing removes, longest first.

    Raw value, its 8-character prefix when the value is longer than that,
    the reversed text, base64 (standard and urlsafe, padded and unpadded),
    URL-encoding (``quote`` and ``quote_plus``), hex (lower and upper), and
    the basic-auth ``username:secret`` blob in the same base64 forms.
    """
    found: list[str] = []
    seen: set[str] = set()
    _add(found, seen, secret, minimum=1)
    if len(secret) > 8:
        _add(found, seen, secret[:8], minimum=1)
    if len(secret) >= 4:
        _add(found, seen, secret[::-1], minimum=4)

    def _encoded(blob: bytes) -> None:
        for encoder in (base64.b64encode, base64.urlsafe_b64encode):
            token = encoder(blob).decode("ascii")
            _add(found, seen, token, minimum=_MIN_ENCODED)
            _add(found, seen, token.rstrip("="), minimum=_MIN_ENCODED)

    raw = secret.encode("utf-8")
    _encoded(raw)
    _add(found, seen, quote(secret, safe=""), minimum=_MIN_ENCODED)
    _add(found, seen, quote_plus(secret), minimum=_MIN_ENCODED)
    _add(found, seen, quote(secret), minimum=_MIN_ENCODED)
    _add(found, seen, raw.hex(), minimum=_MIN_ENCODED)
    _add(found, seen, raw.hex().upper(), minimum=_MIN_ENCODED)
    if username is not None:
        _encoded(f"{username}:{secret}".encode("utf-8"))
    found.sort(key=len, reverse=True)
    return found


def redact_text(text: str, secret: str, *, username: str | None = None) -> str:
    """Replace every known form of ``secret`` in ``text`` with ``[REDACTED]``.

    One pass, longest form first, so an 8-character prefix cannot eat the
    front of the full value and leave the rest visible. Replacement text is
    not scanned again.
    """
    if not text or not secret:
        return text
    forms = secret_forms(secret, username=username)
    if not forms:
        return text
    pattern = re.compile("|".join(re.escape(form) for form in forms))
    return pattern.sub("[REDACTED]", text)


def redact_obj(value, secret: str, *, username: str | None = None):
    """Walk a JSON-shaped object and scrub every string."""
    if not secret:
        return value
    forms = secret_forms(secret, username=username)
    if not forms:
        return value
    pattern = re.compile("|".join(re.escape(form) for form in forms))

    def _scrub(item):
        if isinstance(item, str):
            return pattern.sub("[REDACTED]", item)
        if isinstance(item, dict):
            return {_scrub(key): _scrub(val) for key, val in item.items()}
        if isinstance(item, list):
            return [_scrub(val) for val in item]
        if isinstance(item, tuple):
            return [_scrub(val) for val in item]
        return item

    return _scrub(value)
