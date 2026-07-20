"""Score two person names (with optional birth years) and classify the result.

A few starting heuristics, ordered by strength:
1. Surname gate — a different surname means different people.
2. Given-name compatibility — exact given names, or one side's initials being a
   prefix of the other's ("J. S." vs "Johann Sebastian"), or one side having no
   given names at all (surname-only — plausible but ambiguous, so review).
3. Birth-year corroboration — a conflicting year overrides everything (different
   people); an agreeing year nudges toward auto.

Thresholds are stricter than the work matcher because a wrong person-merge is
costlier. Everything here is a pure function of the parsed names, so the dedupe
pass and the tests can call it directly.
"""

from __future__ import annotations

from .extract import PersonName

AUTO_THRESHOLD = 0.90
REVIEW_THRESHOLD = 0.70


def _initials_compatible(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """True if the shorter initials sequence is a prefix of the longer one."""
    if not a or not b:
        return False
    short, long = sorted((a, b), key=len)
    return long[: len(short)] == short


def _given_score(a: PersonName, b: PersonName) -> tuple[float, str]:
    if a.given and b.given and a.given == b.given:
        return 0.95, "exact_given"
    if _initials_compatible(a.given_initials, b.given_initials):
        return 0.90, "initials"
    if not a.given or not b.given:
        return 0.70, "surname_only"
    return 0.20, "given_conflict"


def _score_pair(
    a: PersonName, b: PersonName, a_year: int | None = None, b_year: int | None = None
) -> tuple[float, str]:
    if a.surname != b.surname:
        return 0.0, "surname_gate"

    base, method = _given_score(a, b)

    if a_year is not None and b_year is not None:
        if abs(a_year - b_year) > 1:
            return 0.05, "year_conflict"  # same name, different lifetime -> different people
        # a matching birth year is strong corroboration: enough to lift a
        # surname-only pair (0.70) over the auto threshold (round to dodge float
        # error: 0.70 + 0.2 == 0.8999...).
        return round(min(1.0, base + 0.2), 4), method

    return base, method


def score(  # noqa: PLR0913
    a: PersonName,
    b: PersonName,
    a_year: int | None = None,
    b_year: int | None = None,
    a_aliases: list[PersonName] | None = None,
    b_aliases: list[PersonName] | None = None,
) -> tuple[float, str]:
    """Similarity of two people in [0, 1] with the method that decided it."""
    best_score = -1.0
    best_method = ""

    a_names = [a] + (a_aliases or [])
    b_names = [b] + (b_aliases or [])

    for an in a_names:
        for bn in b_names:
            val, meth = _score_pair(an, bn, a_year, b_year)
            if val > best_score:
                best_score = val
                best_method = meth

    return best_score, best_method


def classify(value: float) -> str:
    if value >= AUTO_THRESHOLD:
        return "auto_linked"
    if value >= REVIEW_THRESHOLD:
        return "needs_review"
    return "distinct"
