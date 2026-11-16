"""Cryptographically secure passwords for ``generate_and_store_password``.

Generation uses :mod:`secrets` only. Each enabled class contributes at least
one character, then :class:`secrets.SystemRandom` shuffles the result.

The default symbol alphabet is the CLI's safe set from psamvault-cli pull
request 109: ``!@^*()-_=+[]{}:,.``. Left out of :data:`string.punctuation` are quotes,
backtick, backslash, ``$``, ``%``, ``&``, ``|``, ``;``, ``<``, ``>``, ``#``,
``?``, ``/``, ``~``, and space. The CLI flag ``--all-symbols`` selects the
full punctuation set. This tool does not offer that option. Symbols are
always the safe set.

The length default is 20, the same default as ``psamvault generate --length``.
The valid range is 8 to 128. The floor is the higher of 8 and the number of
enabled classes, so the class guarantee is possible and a short password is
refused the same way the CLI refuses it.
"""

from __future__ import annotations

import secrets
import string

# psamvault generate --length default. The floor matches the CLI: max(8, classes).
DEFAULT_LENGTH = 20
MIN_LENGTH = 8
MAX_LENGTH = 128

# Exact safe set from psamvault-cli pull request 109. Not string.punctuation.
SAFE_SYMBOLS = "!@^*()-_=+[]{}:,."
# Characters the CLI names as left out of the default set.
EXCLUDED_SYMBOLS = set("\"'`\\$%&|;<>#?/~ ")

# Same forbidden set as the CLI's vault ``_validate_site_name``.
FORBIDDEN_SITE_CHARS = set("\\/\"'" " <>|?*&#%")

_UPPER = string.ascii_uppercase
_LOWER = string.ascii_lowercase
_DIGITS = string.digits


def enabled_alphabets(
    *,
    upper: bool,
    lower: bool,
    digits: bool,
    symbols: bool,
) -> list[str]:
    """One non-empty alphabet per enabled class, in a stable order."""
    pools: list[str] = []
    if upper:
        pools.append(_UPPER)
    if lower:
        pools.append(_LOWER)
    if digits:
        pools.append(_DIGITS)
    if symbols:
        pools.append(SAFE_SYMBOLS)
    return pools


def site_name_problem(site_name: object) -> str | None:
    """Why ``site_name`` cannot be stored, or None when the CLI would accept it."""
    if not isinstance(site_name, str):
        return "Site name is required."
    if not site_name.strip():
        return "Site name cannot be blank."
    found = [char for char in site_name if char in FORBIDDEN_SITE_CHARS]
    if not found:
        return None
    unique = "".join(dict.fromkeys(found))
    shown = " ".join(repr(char) for char in unique)
    return (
        f"Site name contains invalid character(s): {shown}. "
        "Forbidden characters: \\ / \" ' < > | ? * & # % and space."
    )


def length_floor(enabled: int) -> int:
    """The shortest password the CLI will draw for this many enabled classes."""
    return max(MIN_LENGTH, enabled)


def length_problem(length: object, *, enabled: int) -> str | None:
    """Why ``length`` cannot be used with ``enabled`` classes, or None."""
    if isinstance(length, bool) or not isinstance(length, int):
        return "length must be an integer."
    if enabled < 1:
        return "At least one character class must be enabled."
    floor = length_floor(enabled)
    if length < floor or length > MAX_LENGTH:
        return (
            f"length must be at least {floor} "
            f"(the higher of {MIN_LENGTH} and the number of enabled classes) "
            f"and at most {MAX_LENGTH}."
        )
    return None


def class_flag_problem(name: str, value: object) -> str | None:
    """None when ``value`` is a bool. Callers treat None as the default before this."""
    if isinstance(value, bool):
        return None
    return f"{name} must be a boolean."


def generate_password(
    length: int,
    *,
    upper: bool = True,
    lower: bool = True,
    digits: bool = True,
    symbols: bool = True,
) -> str:
    """Return a password of ``length`` with at least one character from each enabled class.

    Raises:
        ValueError: No class is enabled, or ``length`` cannot satisfy the guarantee.
    """
    pools = enabled_alphabets(upper=upper, lower=lower, digits=digits, symbols=symbols)
    problem = length_problem(length, enabled=len(pools))
    if problem:
        raise ValueError(problem)
    required = [secrets.choice(pool) for pool in pools]
    alphabet = "".join(pools)
    rest = [secrets.choice(alphabet) for _ in range(length - len(pools))]
    chars = required + rest
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)
