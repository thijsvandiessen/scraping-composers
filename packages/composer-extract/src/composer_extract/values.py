"""Coerce extracted literals to the conventions the claims table already uses.

Pages write facts for people to read — "c. 42 minutes", "December 5, 1919" —
while the warehouse stores literals as bare strings that later passes compare and
sort. ``born_on`` is already ISO-8601 everywhere because
``composer_scrapers.wikidata.parse`` truncates to the recorded precision, so a
date the LLM finds has to arrive in the same shape or the two sources will not
line up on one entity.

Nothing here is allowed to guess. A value that does not clearly parse is kept
verbatim: a claim reading "c. 42 minutes" is worth more than a wrong 42, and the
model's exact words also survive in the record's ``raw`` payload either way.
"""

from __future__ import annotations

import re

#: Predicates whose value is a date, and so gets ISO-8601 treatment.
_DATE_PREDICATES = frozenset({"born_on", "died_on", "first_performed_on", "recorded_on"})

#: Predicates whose value is a year. A publisher's catalogue dates an edition and
#: an ensemble's page dates its founding the same loose way a page dates a
#: composition ("first published 1862"), so they are read the same way.
_YEAR_PREDICATES = frozenset({"composed_in", "published_in", "founded_in"})

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
# Month names, each accepting the abbreviation a page may use ("Dec.", "Sept").
# Spelled out rather than generated so the truncations that are actually written
# are the ones that match, and "Marvellous" is not read as March.
_MONTH_NAMES = "|".join(
    (
        r"jan(?:uary)?",
        r"feb(?:ruary)?",
        r"mar(?:ch)?",
        r"apr(?:il)?",
        r"may",
        r"jun(?:e)?",
        r"jul(?:y)?",
        r"aug(?:ust)?",
        r"sep(?:t(?:ember)?)?",
        r"oct(?:ober)?",
        r"nov(?:ember)?",
        r"dec(?:ember)?",
    )
)

_ISO = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")
_MONTH_FIRST = re.compile(rf"\b({_MONTH_NAMES})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)
_DAY_FIRST = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_NAMES})\.?,?\s+(\d{{4}})\b", re.I)
_MONTH_YEAR = re.compile(rf"\b({_MONTH_NAMES})\.?,?\s+(\d{{4}})\b", re.I)
_YEAR = re.compile(r"\b(\d{4})\b")

_HOURS = re.compile(r"(\d+)\s*(?:h\b|hours?\b)", re.I)
_MINUTES = re.compile(r"(\d+)\s*(?:m\b|min\b|mins?\b|minutes?\b)", re.I)
_BARE_NUMBER = re.compile(r"^\D*(\d+)\D*$")


def _month(name: str) -> int:
    """The month number for a full or abbreviated month name."""
    lowered = name.lower()
    return _MONTHS.get(lowered) or next(n for m, n in _MONTHS.items() if m.startswith(lowered[:3]))


def _iso_date(raw: str) -> str | None:
    """*raw* as ISO-8601, truncated to the precision it actually states.

    A day-precision date becomes YYYY-MM-DD, a month YYYY-MM, a bare year YYYY —
    the same precision truncation the wikidata scraper applies, so dates from the
    two sources compare directly.
    """
    text = raw.strip()
    if (iso := _ISO.match(text)) is not None:
        return "-".join(part for part in iso.groups() if part)
    if (match := _MONTH_FIRST.search(text)) is not None:
        month, day, year = match.group(1), int(match.group(2)), match.group(3)
        return f"{year}-{_month(month):02d}-{day:02d}"
    if (match := _DAY_FIRST.search(text)) is not None:
        day, month, year = int(match.group(1)), match.group(2), match.group(3)
        return f"{year}-{_month(month):02d}-{day:02d}"
    if (match := _MONTH_YEAR.search(text)) is not None:
        return f"{match.group(2)}-{_month(match.group(1)):02d}"
    years = _YEAR.findall(text)
    if len(years) == 1:
        return years[0]
    return None


def _minutes(raw: str) -> str | None:
    """A duration in whole minutes: "c. 42 minutes" -> "42", "1h 15m" -> "75"."""
    text = raw.strip()
    hours = _HOURS.search(text)
    minutes = _MINUTES.search(text)
    if hours is not None or minutes is not None:
        total = int(hours.group(1)) * 60 if hours else 0
        total += int(minutes.group(1)) if minutes else 0
        return str(total) if total else None
    # A unitless value on a duration predicate is already a count of minutes.
    if (bare := _BARE_NUMBER.match(text)) is not None:
        return bare.group(1)
    return None


def _year_only(raw: str) -> str | None:
    """The year *raw* states, or ``None`` when it names none or several.

    A range ("1804-1806") is deliberately left verbatim rather than resolved to
    one end of it — which end is a judgement this layer has no basis to make.
    """
    years = _YEAR.findall(raw)
    return years[0] if len(years) == 1 else None


def coerce_value(predicate: str, raw: str) -> str:
    """*raw* in the shape the claims table stores for *predicate*.

    Falls back to the stripped original whenever the value does not parse
    cleanly, so nothing is ever lost to normalization.
    """
    text = raw.strip()
    if not text:
        return text
    if predicate in _DATE_PREDICATES:
        return _iso_date(text) or text
    if predicate == "duration_minutes":
        return _minutes(text) or text
    if predicate in _YEAR_PREDICATES:
        return _year_only(text) or text
    return text
