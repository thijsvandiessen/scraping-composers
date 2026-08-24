"""Derive concerts from the mentions' raw performance context.

A post-hoc pass over the silver database, like ``dedupe_persons``: work
mentions carry each source's full performance payload in
``raw_work_mentions.raw``; this pass groups them into concerts per source,
resolves conductor, soloist and ensemble names to entities by normalized name,
and links each concert to its programme. Re-running rebuilds the concert
tables from scratch, so the pass can be repeated after new loads.

Participants resolve against *all* person and ensemble entities — silver keeps
duplicate spellings side by side; the gold promote step re-points them to
canonical roots when it copies the tables.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from composer_models import Concert, ConcertParticipant, ConcertWork, Entity, RawWorkMention, Source
from composer_models.db import resync_pk_sequence
from composer_models.normalize import dedup_key
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from .payloads import concert_fields

INSERT_BATCH = 1000


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
        fields = concert_fields(source_names.get(source_id, ""), json.loads(raw))
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
                "ensembles": set(),
                "mention_ids": [],
            },
        )
        concert["conductors"].update(fields.conductors)
        concert["ensembles"].update(fields.ensembles)
        for soloist_name, discipline in fields.soloists:
            if concert["soloists"].get(soloist_name) is None:
                concert["soloists"][soloist_name] = discipline
        concert["mention_ids"].append(mention_id)
    return concerts


@dataclass
class _RowBatch:
    """Accumulates the insert rows plus participant-resolution stats."""

    person_by_key: dict[str, uuid.UUID]
    ensemble_by_key: dict[str, uuid.UUID] = field(default_factory=dict)
    concerts: list[dict[str, Any]] = field(default_factory=list)
    participants: list[dict[str, Any]] = field(default_factory=list)
    works: list[dict[str, Any]] = field(default_factory=list)
    participant_links: int = 0
    unresolved_names: set[str] = field(default_factory=set)

    def _resolve(self, role: str, key: str) -> uuid.UUID | None:
        """The entity a credited name refers to.

        Both maps are consulted whatever the role: a credit's slot says how the
        source filed the name, not what the name is — sources list choirs and
        piano trios among the soloists — and ingest kinds a name that reads as
        an ensemble as one (see :func:`~composer_schema.resolve_entity_kind`).
        The role only decides which map is asked first.
        """
        if role == "ensemble":
            return self.ensemble_by_key.get(key) or self.person_by_key.get(key)
        return self.person_by_key.get(key) or self.ensemble_by_key.get(key)

    def add_participant(self, concert_id: int, role: str, name: str, discipline: str | None) -> None:
        resolved = self._resolve(role, dedup_key(name))
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
        for name in sorted(data["ensembles"]):
            self.add_participant(concert_id, "ensemble", name, None)
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
    ensemble_by_key: dict[str, uuid.UUID] = {
        key: entity_id
        for entity_id, key in session.execute(
            select(Entity.id, Entity.dedup_key).where(Entity.kind == "ensemble")
        ).tuples()
    }

    rows = _RowBatch(person_by_key, ensemble_by_key)
    for concert_id, (key, data) in enumerate(sorted(_group_concerts(session).items()), start=1):
        rows.add_concert(concert_id, key, data)

    for i in range(0, len(rows.concerts), INSERT_BATCH):
        session.execute(insert(Concert), rows.concerts[i : i + INSERT_BATCH])
    for i in range(0, len(rows.participants), INSERT_BATCH):
        session.execute(insert(ConcertParticipant), rows.participants[i : i + INSERT_BATCH])
    for i in range(0, len(rows.works), INSERT_BATCH):
        session.execute(insert(ConcertWork), rows.works[i : i + INSERT_BATCH])
    # The ids above were assigned explicitly, so the sequence never advanced.
    resync_pk_sequence(session, Concert.__tablename__)
    session.commit()

    return DeriveConcertsStats(
        concerts=len(rows.concerts),
        participant_links=rows.participant_links,
        unresolved_participant_names=len(rows.unresolved_names),
    )
