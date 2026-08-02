"""Per-source readers for the concert context carried in a mention's payload.

Every performance source encodes a concert differently — its own identity key,
date format, and credit fields — so each gets a small reader that returns the
one source-independent shape :mod:`composer_warehouse.concerts.derive` groups
on. A source with no reader (and no LLM marker) yields no concerts at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_DDMMYYYY = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")


def iso_date(value: str | None) -> str | None:
    """Normalize DD-MM-YYYY (concertgebouw) to ISO; pass other formats through."""
    if not value:
        return None
    match = _DDMMYYYY.match(value)
    if match:
        day, month, year = match.groups()
        return f"{year}-{month}-{day}"
    return value


@dataclass(frozen=True)
class ConcertFields:
    """One mention's concert-level payload, in a source-independent shape."""

    external_key: str
    date: str | None
    venue: str | None
    season: str | None
    event_type: str | None
    url: str | None
    conductors: tuple[str, ...]
    soloists: tuple[tuple[str, str | None], ...]  # (name, discipline)
    ensembles: tuple[str, ...] = ()  # orchestras/choirs, where the source names them


def _soloists(raw: dict[str, Any]) -> tuple[tuple[str, str | None], ...]:
    # every source reports soloists as {"name": ..., "discipline": ...}
    return tuple(
        (s["name"], s.get("discipline"))
        for s in raw.get("soloists") or []
        if isinstance(s, dict) and s.get("name")
    )


def _concertgebouw_fields(raw: dict[str, Any]) -> ConcertFields | None:
    date = iso_date(raw.get("date"))
    city = raw.get("city")
    if not date:
        return None
    conductor = raw.get("conductor")
    return ConcertFields(
        external_key=f"{date}|{city or ''}",
        date=date,
        venue=city,
        season=None,
        event_type=None,
        url=None,
        conductors=(conductor,) if conductor else (),
        soloists=_soloists(raw),
    )


def _nyphil_fields(raw: dict[str, Any]) -> ConcertFields | None:
    program = raw.get("programID")
    date = raw.get("date")
    if not program or not date:
        return None
    venue = ", ".join(part for part in (raw.get("venue"), raw.get("location")) if part) or None
    return ConcertFields(
        external_key=f"{program}|{date}",
        date=date,
        venue=venue,
        season=raw.get("season"),
        event_type=raw.get("eventType"),
        url=None,
        conductors=tuple(raw.get("conductors") or ()),
        soloists=_soloists(raw),
    )


def _berlinphil_fields(raw: dict[str, Any]) -> ConcertFields | None:
    concert_id = raw.get("concert_id")
    if not concert_id:
        return None
    return ConcertFields(
        external_key=str(concert_id),
        date=raw.get("date"),
        venue=None,
        season=raw.get("season"),
        event_type=None,
        url=raw.get("url"),
        conductors=tuple(raw.get("conductors") or ()),
        soloists=_soloists(raw),
        ensembles=tuple(name for name in raw.get("ensembles") or () if name),
    )


def _rco_fields(raw: dict[str, Any]) -> ConcertFields | None:
    """RCO mentions carry the concert-level credits on every work of the concert."""
    concert_id = raw.get("concert_id")
    if not concert_id:
        return None
    conductor = raw.get("conductor")
    return ConcertFields(
        external_key=str(concert_id),
        date=iso_date((raw.get("date") or "").split("T", 1)[0] or None),
        venue=raw.get("venue") or None,
        season=None,
        event_type=None,
        url=raw.get("url") or None,
        conductors=(conductor,) if conductor else (),
        soloists=_soloists(raw),
    )


def _llm_fields(raw: dict[str, Any]) -> ConcertFields | None:
    """Concert fields from an LLM-extracted mention (composer_extract writes a
    normalized, source-independent payload marked ``_source: "llm"``)."""
    key = raw.get("concert_key")
    if not key:
        return None
    return ConcertFields(
        external_key=str(key),
        date=raw.get("date"),
        venue=raw.get("venue"),
        season=raw.get("season"),
        event_type=raw.get("event_type"),
        url=raw.get("url"),
        conductors=tuple(raw.get("conductors") or ()),
        soloists=_soloists(raw),
    )


#: The ``_kind`` markers on an LLM payload that mean "a concert". The concert
#: extractor predates the marker and writes none, so ``None`` counts too.
_CONCERT_KINDS = (None, "concert")

# Each performance source encodes concert identity differently; sources not
# listed here yield no concerts.
_SOURCE_FIELDS = {
    "concertgebouw_archive": _concertgebouw_fields,
    "nyphil": _nyphil_fields,
    "berlinphil": _berlinphil_fields,
    "rco": _rco_fields,
}


def concert_fields(source_name: str, raw: dict[str, Any]) -> ConcertFields | None:
    """Concert identity and fields for one mention's payload, or None for
    unknown sources / payloads without a usable concert identity."""
    parse = _SOURCE_FIELDS.get(source_name)
    if parse:
        return parse(raw)
    # LLM-extracted mentions carry a normalized payload regardless of the site
    # they were crawled from, so they resolve by marker rather than source name.
    # Every extract kind shares the "llm" marker and is told apart by "_kind":
    # recordings belong to derive_recordings, work profiles to no derive pass at
    # all. Matched positively, the way derive_recordings does it, so a kind added
    # later is ignored here rather than mistaken for a concert.
    if raw.get("_source") == "llm" and raw.get("_kind") in _CONCERT_KINDS:
        return _llm_fields(raw)
    return None
