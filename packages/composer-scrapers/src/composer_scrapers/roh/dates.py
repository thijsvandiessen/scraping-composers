"""Turning the archive's printed dates into ISO 8601.

The database prints one date format throughout — "14 January 1947", "31 March
2009" — and two degenerate cases: a bare year where the day is unknown ("1941",
the premiere of the Kirov *Bayadère*), and a date with a place glued to it in
the premiere fields ("24 December 1871, Opera House, Cairo"). All three are
handled here so a caller can ask for the date without knowing which field it
came from.

**Precision is preserved, not invented.** A bare year comes back as ``1941``,
not ``1941-01-01``: this is a heritage catalogue where "the day is not recorded"
is a real and common state, and a fabricated 1 January would be indistinguishable
downstream from a genuine New Year's Day performance. :func:`iso_date` returns
whatever precision the source stated, which is what the ``born_on``/``died_on``
claims elsewhere in this repository already do with partial dates.

**Nothing is guessed from an unparseable string.** The premiere fields are free
text and some of them say things like "Information on premiere not readily
available"; those return None rather than the first four-digit number in the
sentence, which would happily read a catalogue number as a year.
"""

from __future__ import annotations

import re

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

#: "14 January 1947", anchored at the start of the field so that a date buried
#: mid-sentence in a note is not mistaken for the field's own date.
_FULL_RE = re.compile(
    rf"^\s*(\d{{1,2}})\s+({'|'.join(_MONTHS)})\s+(\d{{4}})\b",
    re.IGNORECASE,
)

#: "March 1965" — month and year, no day.
_MONTH_RE = re.compile(rf"^\s*({'|'.join(_MONTHS)})\s+(\d{{4}})\b", re.IGNORECASE)

#: A bare year, and nothing before it.
_YEAR_RE = re.compile(r"^\s*(\d{4})\b")


def iso_date(value: str | None) -> str | None:
    """*value* as ``YYYY-MM-DD``, ``YYYY-MM`` or ``YYYY``, or None if it states no date.

    The precision returned is the precision the source stated — see the module
    docstring on why a bare year is not padded out to a day.
    """
    if not value:
        return None
    full = _FULL_RE.match(value)
    if full is not None:
        day, month, year = full.groups()
        return f"{int(year):04d}-{_MONTHS[month.casefold()]:02d}-{int(day):02d}"
    month_only = _MONTH_RE.match(value)
    if month_only is not None:
        month, year = month_only.groups()
        return f"{int(year):04d}-{_MONTHS[month.casefold()]:02d}"
    year_only = _YEAR_RE.match(value)
    return f"{int(year_only.group(1)):04d}" if year_only is not None else None


def place(value: str | None) -> str | None:
    """Whatever follows the date in a premiere field, e.g. "Opera House, Cairo".

    The premiere fields are written "<date>, <company and venue>"; this returns
    the second half, or None when the field is a date alone or no date at all.
    """
    if not value or iso_date(value) is None:
        return None
    _, sep, rest = value.partition(",")
    return rest.strip() or None if sep else None
