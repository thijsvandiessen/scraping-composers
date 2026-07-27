"""Derive concerts from the mentions' raw performance context.

A post-hoc pass over the silver database, like ``dedupe_persons``: work
mentions carry each source's full performance payload in
``raw_work_mentions.raw``; this pass groups them into concerts per source,
resolves conductor and soloist names to person entities by normalized name,
and links each concert to its programme. Re-running rebuilds the concert
tables from scratch, so the pass can be repeated after new loads.

Participants resolve against *all* person entities — silver keeps duplicate
spellings side by side; the gold promote step re-points them to canonical
roots when it copies the tables.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from ..models import Concert, ConcertParticipant, ConcertWork, Entity, RawWorkMention, Source
from ..normalize import dedup_key

INSERT_BATCH = 1000

_DDMMYYYY = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")


def _iso_date(value: str | None) -> str | None:
    """Normalize DD-MM-YYYY (concertgebouw) to ISO; pass other formats through."""
    if not value:
        return None
    match = _DDMMYYYY.match(value)
    if match:
        day, month, year = match.groups()
        return f"{year}-{month}-{day}"
    return value


@dataclass(frozen=True)
class _ConcertFields:
    """One mention's concert-level payload, in a source-independent shape."""

    external_key: str
    date: str | None
    venue: str | None
    season: str | None
    event_type: str | None
    url: str | None
    conductors: tuple[str, ...]
    soloists: tuple[tuple[str, str | None], ...]  # (name, discipline)


def _soloists(raw: dict[str, Any]) -> tuple[tuple[str, str | None], ...]:
    # all three sources report soloists as {"name": ..., "discipline": ...}
    return tuple(
        (s["name"], s.get("discipline"))
        for s in raw.get("soloists") or []
        if isinstance(s, dict) and s.get("name")
    )


def _concertgebouw_fields(raw: dict[str, Any]) -> _ConcertFields | None:
    date = _iso_date(raw.get("date"))
    city = raw.get("city")
    if not date:
        return None
    conductor = raw.get("conductor")
    return _ConcertFields(
        external_key=f"{date}|{city or ''}",
        date=date,
        venue=city,
        season=None,
        event_type=None,
        url=None,
        conductors=(conductor,) if conductor else (),
        soloists=_soloists(raw),
    )


def _nyphil_fields(raw: dict[str, Any]) -> _ConcertFields | None:
    program = raw.get("programID")
    date = raw.get("date")
    if not program or not date:
        return None
    venue = ", ".join(part for part in (raw.get("venue"), raw.get("location")) if part) or None
    return _ConcertFields(
        external_key=f"{program}|{date}",
        date=date,
        venue=venue,
        season=raw.get("season"),
        event_type=raw.get("eventType"),
        url=None,
        conductors=tuple(raw.get("conductors") or ()),
        soloists=_soloists(raw),
    )


def _berlinphil_fields(raw: dict[str, Any]) -> _ConcertFields | None:
    concert_id = raw.get("concert_id")
    if not concert_id:
        return None
    return _ConcertFields(
        external_key=str(concert_id),
        date=raw.get("date"),
        venue=None,
        season=raw.get("season"),
        event_type=None,
        url=raw.get("url"),
        conductors=tuple(raw.get("conductors") or ()),
        soloists=_soloists(raw),
    )


def _llm_fields(raw: dict[str, Any]) -> _ConcertFields | None:
    """Concert fields from an LLM-extracted mention (composer_extract writes a
    normalized, source-independent payload marked ``_source: "llm"``)."""
    key = raw.get("concert_key")
    if not key:
        return None
    return _ConcertFields(
        external_key=str(key),
        date=raw.get("date"),
        venue=raw.get("venue"),
        season=raw.get("season"),
        event_type=raw.get("event_type"),
        url=raw.get("url"),
        conductors=tuple(raw.get("conductors") or ()),
        soloists=_soloists(raw),
    )


# Each performance source encodes concert identity differently; sources not
# listed here yield no concerts.
_SOURCE_FIELDS = {
    "concertgebouw_archive": _concertgebouw_fields,
    "nyphil": _nyphil_fields,
    "berlinphil": _berlinphil_fields,
}


def _concert_fields(source_name: str, raw: dict[str, Any]) -> _ConcertFields | None:
    """Concert identity and fields for one mention's payload, or None for
    unknown sources / payloads without a usable concert identity."""
    parse = _SOURCE_FIELDS.get(source_name)
    if parse:
        return parse(raw)
    # LLM-extracted mentions carry a normalized payload regardless of the site
    # they were crawled from, so they resolve by marker rather than source name.
    # Recording payloads share the "llm" marker but belong to derive_recordings.
    if raw.get("_source") == "llm" and raw.get("_kind") != "recording":
        return _llm_fields(raw)
    return None


@dataclass(frozen=True)
class DeriveConcertsStats:
    concerts: int = 0
    participant_links: int = 0
    unresolved_participant_names: int = 0


def _group_concerts(session: Session) -> dict[tuple[int, str], dict[str, Any]]:
    """Fold all work mentions into concerts keyed by (source, external key)."""
    source_names: dict[int, str] = {
        source_id: name for source_id, name in session.execute(select(Source.id, Source.name)).tuples()
    }
    concerts: dict[tuple[int, str], dict[str, Any]] = {}
    for mention_id, source_id, raw in session.execute(
        select(RawWorkMention.id, RawWorkMention.source_id, RawWorkMention.raw)
    ).tuples():
        fields = _concert_fields(source_names.get(source_id, ""), json.loads(raw))
        if fields is None:
            continue
        concert = concerts.setdefault(
            (source_id, fields.external_key),
            {
                "date": fields.date,
                "venue": fields.venue,
                "season": fields.season,
                "event_type": fields.event_type,
                "url": fields.url,
                "conductors": set(),
                "soloists": {},  # name -> discipline (first non-null wins)
                "mention_ids": [],
            },
        )
        concert["conductors"].update(fields.conductors)
        for soloist_name, discipline in fields.soloists:
            if concert["soloists"].get(soloist_name) is None:
                concert["soloists"][soloist_name] = discipline
        concert["mention_ids"].append(mention_id)
    return concerts


@dataclass
class _RowBatch:
    """Accumulates the insert rows plus participant-resolution stats."""

    person_by_key: dict[str, uuid.UUID]
    concerts: list[dict[str, Any]] = field(default_factory=list)
    participants: list[dict[str, Any]] = field(default_factory=list)
    works: list[dict[str, Any]] = field(default_factory=list)
    participant_links: int = 0
    unresolved_names: set[str] = field(default_factory=set)

    def add_participant(self, concert_id: int, role: str, name: str, discipline: str | None) -> None:
        resolved = self.person_by_key.get(dedup_key(name))
        if resolved is not None:
            self.participant_links += 1
        else:
            self.unresolved_names.add(name)
        self.participants.append(
            {
                "concert_id": concert_id,
                "role": role,
                "name": name,
                "discipline": discipline,
                "entity_id": resolved,
            }
        )

    def add_concert(self, concert_id: int, key: tuple[int, str], data: dict[str, Any]) -> None:
        source_id, external_key = key
        self.concerts.append(
            {
                "id": concert_id,
                "source_id": source_id,
                "external_key": external_key,
                "date": data["date"],
                "venue": data["venue"],
                "season": data["season"],
                "event_type": data["event_type"],
                "url": data["url"],
            }
        )
        for name in sorted(data["conductors"]):
            self.add_participant(concert_id, "conductor", name, None)
        for name in sorted(data["soloists"]):
            self.add_participant(concert_id, "soloist", name, data["soloists"][name])
        self.works.extend(
            {"concert_id": concert_id, "mention_id": mention_id} for mention_id in data["mention_ids"]
        )


def derive_concerts(session: Session) -> DeriveConcertsStats:
    """Rebuild the concert tables from the work mentions' raw payloads."""
    session.execute(delete(ConcertWork))
    session.execute(delete(ConcertParticipant))
    session.execute(delete(Concert))

    person_by_key: dict[str, uuid.UUID] = {
        key: entity_id
        for entity_id, key in session.execute(
            select(Entity.id, Entity.dedup_key).where(Entity.kind == "person")
        ).tuples()
    }

    rows = _RowBatch(person_by_key)
    for concert_id, (key, data) in enumerate(sorted(_group_concerts(session).items()), start=1):
        rows.add_concert(concert_id, key, data)

    for i in range(0, len(rows.concerts), INSERT_BATCH):
        session.execute(insert(Concert), rows.concerts[i : i + INSERT_BATCH])
    for i in range(0, len(rows.participants), INSERT_BATCH):
        session.execute(insert(ConcertParticipant), rows.participants[i : i + INSERT_BATCH])
    for i in range(0, len(rows.works), INSERT_BATCH):
        session.execute(insert(ConcertWork), rows.works[i : i + INSERT_BATCH])
    session.commit()

    return DeriveConcertsStats(
        concerts=len(rows.concerts),
        participant_links=rows.participant_links,
        unresolved_participant_names=len(rows.unresolved_names),
    )
