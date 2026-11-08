"""The premiere convention on work mentions.

A work mention that knows when (and where) its work was first performed says so
under ``raw["premiere"]``::

    {"premiere": {"date": "1824-05-07", "place": "Theater am Kärntnertor"}}

``date`` is an ISO date at the precision the source states it (``1824``,
``1824-05`` or ``1824-05-07``, with a leading ``-`` for BCE) and ``place`` is a
free-text place or ``None``. The key means the **world premiere** of the work: a
house or production premiere is a fact about one staging, not about the work,
and must never be written here (the ROH keeps those under its own
``raw["premieres"]``).
"""

from __future__ import annotations

import re
from typing import Any

PREMIERE_KEY = "premiere"

_DATE = re.compile(r"(-?)(\d{1,4})(?:-(\d{2})(?:-(\d{2}))?)?")


def date_parts(date: str) -> tuple[int, ...] | None:
    """``date`` as (year, month, day) components, as many as it states, or None
    if it is not an ISO date at year, month or day precision."""
    match = _DATE.fullmatch(date.strip())
    if match is None:
        return None
    sign, year, month, day = match.groups()
    parts = [-int(year) if sign else int(year)]
    if month is not None:
        parts.append(int(month))
        if day is not None:
            parts.append(int(day))
    return tuple(parts)


def premiere_of(raw: dict[str, Any]) -> tuple[str, str | None] | None:
    """The (date, place) a mention's ``raw`` states for its work's world
    premiere, or None when it states none or states it in a shape that isn't
    the convention (a date that isn't ISO is not guessed at)."""
    premiere = raw.get(PREMIERE_KEY)
    if not isinstance(premiere, dict):
        return None
    date = premiere.get("date")
    if not isinstance(date, str) or date_parts(date) is None:
        return None
    place = premiere.get("place")
    if not isinstance(place, str) or not place.strip():
        place = None
    return date.strip(), place.strip() if place else None


def supersedes(new: str, current: str | None) -> bool:
    """Whether premiere date ``new`` should replace ``current``.

    The earlier date wins: a later "premiere" is almost always a house
    premiere mislabelled as the world one. Where one date only refines the
    other (``1824`` and ``1824-05-07``) the more precise one wins. Neither rule
    depends on the order the sources arrive in, so rebuilding from bronze
    settles on the same date."""
    new_parts = date_parts(new)
    if new_parts is None:
        return False
    current_parts = date_parts(current) if current is not None else None
    if current_parts is None:
        return True
    shared = min(len(new_parts), len(current_parts))
    if new_parts[:shared] != current_parts[:shared]:
        return new_parts[:shared] < current_parts[:shared]
    return len(new_parts) > len(current_parts)
