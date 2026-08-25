"""Turn a pair of person profiles into a discrete comparison vector.

Fellegi-Sunter needs each pair reduced to a handful of *ordinal levels* — one
per comparison column — whose match/non-match likelihoods can be estimated.
Nothing here decides anything; it only describes how two records agree, and
:mod:`fellegi_sunter` supplies the weights.

The given-name column is where the old scorer was wrong (#173). It compared
initials *instead of* the spelled-out names, so ``Jules`` and ``Jochen`` both
reduced to ``("j",)`` and agreed. The fix is representational: given names are
aligned position-wise, and a position only counts as abbreviated when one side
actually *is* an abbreviation. Two spelled-out tokens must be equal.
"""

from __future__ import annotations

from enum import IntEnum

from .extract import PersonName


class GivenLevel(IntEnum):
    """How two given-name sequences agree, weakest to strongest."""

    CONFLICT = 0
    """At least one aligned position disagrees ("Jules" vs "Jochen")."""

    ABSENT = 1
    """One or both sides are surname-only — no evidence either way."""

    INITIALS = 2
    """Every aligned position is compatible, at least one via an
    abbreviation ("J. S." vs "Johann Sebastian")."""

    PREFIX = 3
    """Every aligned position is spelled out and equal, but one side carries
    extra given names ("Johann" vs "Johann Sebastian")."""

    EXACT = 4
    """Identical given-name sequences, all spelled out."""


class YearLevel(IntEnum):
    """How two four-digit years agree. ``ABSENT`` means at least one side has
    no year at all, which is no evidence rather than weak evidence."""

    CONFLICT = 0
    """More than a decade apart — a different lifetime (a father, not a son)."""

    DISTANT = 1
    """3-10 years apart: too far for a source disagreement, close enough that
    it happens between relatives."""

    CLOSE = 2
    """1-2 years apart — the usual disagreement between sources."""

    EXACT = 3
    """The same year."""

    ABSENT = 4
    """One or both sides have no year."""


def _token_agrees(a: str, b: str) -> bool | None:
    """Whether two aligned given-name tokens agree.

    ``None`` when the comparison needed an abbreviation to succeed, so the
    caller can tell ``INITIALS`` from ``EXACT``.

    Tokenising drops the full stop, so "Ed." and "Ed" are indistinguishable by
    the time we get here. A token counts as an abbreviation only when it is at
    most two characters *and* a prefix of the token it faces: "j" abbreviates
    "johann" and "ed" abbreviates "edward", but "ed" against "ella" is two
    different names. Three characters is where the risk of swallowing a real
    name ("jan" against "janice") outweighs the abbreviations still uncaught.
    """
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) <= 2 and long.startswith(short):
        return None
    return False


def given_level(a: PersonName, b: PersonName) -> GivenLevel:
    """The :class:`GivenLevel` for two parsed names."""
    if not a.given or not b.given:
        return GivenLevel.ABSENT

    abbreviated = False
    for token_a, token_b in zip(a.given, b.given, strict=False):
        agrees = _token_agrees(token_a, token_b)
        if agrees is False:
            return GivenLevel.CONFLICT
        if agrees is None:
            abbreviated = True

    if abbreviated:
        return GivenLevel.INITIALS
    if len(a.given) != len(b.given):
        return GivenLevel.PREFIX
    return GivenLevel.EXACT


def year_level(a: int | None, b: int | None) -> YearLevel:
    """The :class:`YearLevel` for two optional years."""
    if a is None or b is None:
        return YearLevel.ABSENT
    gap = abs(a - b)
    if gap == 0:
        return YearLevel.EXACT
    if gap <= 2:
        return YearLevel.CLOSE
    if gap <= 10:
        return YearLevel.DISTANT
    return YearLevel.CONFLICT
