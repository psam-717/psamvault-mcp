"""Version ordering, in one place.

Two modules need to answer "is what is published newer than what I have?" — the startup notice
(``version_check``) and the lockstep check (``compat``). They used to answer it with two different
comparators, one of which collapsed any suffixed version to ``(0,)``. This module is the single
comparator, so the two can never disagree.

Ordering rules (deliberately simple and predictable):

* numeric release parts compare first, so ``0.5.10`` > ``0.5.9`` (a string compare would get this wrong)
* a **pre-release** suffix sorts *below* its release: ``0.5.3rc1`` < ``0.5.3``
* a **post** suffix sorts *above* its release: ``0.5.3.post1`` > ``0.5.3``
* anything unparseable degrades to ``(0, 0, 0, 1)`` — never raises, because a version string coming
  from the network must not be able to crash a release check
"""

from __future__ import annotations

import re

_PRE_RE = re.compile(r"^(a|b|c|rc|alpha|beta|pre|preview|dev)\d*$")
_POST_RE = re.compile(r"^(post|rev|r)\d*$")

# rank: pre-release < final release < post-release
_RANK_PRE = 0
_RANK_FINAL = 1
_RANK_POST = 2

_SPLIT = re.compile(r"^(\d+(?:\.\d+)*)(.*)$")


def vkey(version: str) -> tuple[int, ...]:
    """Comparable key for a version string. Never raises."""
    text = str(version or "").strip().lstrip("v")
    match = _SPLIT.match(text)
    if not match:
        return (0, 0, 0, _RANK_FINAL)

    nums = [int(p) for p in match.group(1).split(".")]
    overflow = len(nums) > 3  # 1.2.3.4 style: extra numeric part means a post/build release
    while len(nums) < 3:
        nums.append(0)
    nums = nums[:3]

    suffix = (match.group(2) or "").strip().lstrip(".-_+").lower()
    if not suffix:
        rank = _RANK_POST if overflow else _RANK_FINAL
    elif _POST_RE.match(suffix):
        rank = _RANK_POST
    elif _PRE_RE.match(suffix):
        rank = _RANK_PRE
    else:
        rank = _RANK_PRE  # unknown suffix: treat as pre-release, i.e. do not claim it is an upgrade

    return (*nums, rank)


def is_newer(candidate: str, current: str) -> bool:
    """True when ``candidate`` is a strictly newer release than ``current``."""
    return vkey(candidate) > vkey(current)
