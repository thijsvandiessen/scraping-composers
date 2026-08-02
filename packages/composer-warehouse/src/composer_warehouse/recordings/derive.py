"""Derive recordings from the mentions' raw release context.

The album/release counterpart to ``derive_concerts``: a post-hoc pass over the
silver database. LLM-extracted work mentions from a *recordings* crawl carry the
release payload in ``raw_work_mentions.raw`` (marked ``_source: "llm"``,
``_kind: "recording"``); this pass groups them into recordings per source,
resolves artist names to person entities by normalized name, and links each
recording to the works on it. Re-running rebuilds the recording tables from
scratch, so the pass can be repeated after new loads.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from ..models import Entity, RawWorkMention, Recording, RecordingParticipant, RecordingWork, Source
from ..normalize import dedup_key

INSERT_BATCH = 1000

_ROLES = frozenset({"conductor", "soloist", "ensemble"})


def _role(value: str | None) -> str:
    """Normalize an artist's reported role to a known label (else 'performer')."""
    normalized = (value or "").strip().lower()
    return normalized if normalized in _ROLES else "performer"


@dataclass(frozen=True)
class _RecordingFields:
    """One mention's recording-level payload, in a source-independent shape."""

    external_key: str
    title: str | None
    release_date: str | None
    label: str | None
    catalogue_number: str | None
    format: str | None
    url: str | None
    participants: tuple[tuple[str, str, str | None], ...]  # (name, role, discipline)


def _participants(raw: dict[str, Any]) -> tuple[tuple[str, str, str | None], ...]:
    return tuple(
        (a["name"], _role(a.get("role")), a.get("discipline"))
        for a in raw.get("artists") or []
        if isinstance(a, dict) and a.get("name")
    )


def _recording_fields(source_name: str, raw: dict[str, Any]) -> _RecordingFields | None:
    """Recording identity and fields for one mention's payload, or None when the
    payload is not an LLM-extracted recording with a usable identity."""
    if raw.get("_source") != "llm" or raw.get("_kind") != "recording":
        return None
    key = raw.get("record_key")
    if not key:
        return None
    return _RecordingFields(
        external_key=str(key),
        title=raw.get("title"),
        release_date=raw.get("release_date"),
        label=raw.get("label"),
        catalogue_number=raw.get("catalogue_number"),
        format=raw.get("format"),
        url=raw.get("url"),
        participants=_participants(raw),
    )


@dataclass(frozen=True)
class DeriveRecordingsStats:
    recordings: int = 0
    participant_links: int = 0
    unresolved_participant_names: int = 0


def _group_recordings(session: Session) -> dict[tuple[int, str], dict[str, Any]]:
    """Fold all work mentions into recordings keyed by (source, external key)."""
    source_names: dict[int, str] = {
        source_id: name for source_id, name in session.execute(select(Source.id, Source.name)).tuples()
    }
    recordings: dict[tuple[int, str], dict[str, Any]] = {}
    for mention_id, source_id, raw in session.execute(
        select(RawWorkMention.id, RawWorkMention.source_id, RawWorkMention.raw)
    ).tuples():
        fields = _recording_fields(source_names.get(source_id, ""), json.loads(raw))
        if fields is None:
            continue
        recording = recordings.setdefault(
            (source_id, fields.external_key),
            {
                "title": fields.title,
                "release_date": fields.release_date,
                "label": fields.label,
                "catalogue_number": fields.catalogue_number,
                "format": fields.format,
                "url": fields.url,
                "participants": {},  # name -> (role, discipline); first mention wins
                "mention_ids": [],
            },
        )
        for name, role, discipline in fields.participants:
            recording["participants"].setdefault(name, (role, discipline))
        recording["mention_ids"].append(mention_id)
    return recordings


@dataclass
class _RowBatch:
    """Accumulates the insert rows plus participant-resolution stats."""

    person_by_key: dict[str, uuid.UUID]
    ensemble_by_key: dict[str, uuid.UUID] = field(default_factory=dict)
    recordings: list[dict[str, Any]] = field(default_factory=list)
    participants: list[dict[str, Any]] = field(default_factory=list)
    works: list[dict[str, Any]] = field(default_factory=list)
    participant_links: int = 0
    unresolved_names: set[str] = field(default_factory=set)

    def _resolve(self, role: str, key: str) -> uuid.UUID | None:
        """An ensemble credit prefers an ``ensemble`` entity and falls back to a
        person (the LLM records orchestras as persons); other roles are people."""
        if role == "ensemble":
            return self.ensemble_by_key.get(key) or self.person_by_key.get(key)
        return self.person_by_key.get(key)

    def add_participant(self, recording_id: int, role: str, name: str, discipline: str | None) -> None:
        resolved = self._resolve(role, dedup_key(name))
        if resolved is not None:
            self.participant_links += 1
        else:
            self.unresolved_names.add(name)
        self.participants.append(
            {
                "recording_id": recording_id,
                "role": role,
                "name": name,
                "discipline": discipline,
                "entity_id": resolved,
            }
        )

    def add_recording(self, recording_id: int, key: tuple[int, str], data: dict[str, Any]) -> None:
        source_id, external_key = key
        self.recordings.append(
            {
                "id": recording_id,
                "source_id": source_id,
                "external_key": external_key,
                "title": data["title"],
                "release_date": data["release_date"],
                "label": data["label"],
                "catalogue_number": data["catalogue_number"],
                "format": data["format"],
                "url": data["url"],
            }
        )
        for name in sorted(data["participants"]):
            role, discipline = data["participants"][name]
            self.add_participant(recording_id, role, name, discipline)
        self.works.extend(
            {"recording_id": recording_id, "mention_id": mention_id} for mention_id in data["mention_ids"]
        )


def derive_recordings(session: Session) -> DeriveRecordingsStats:
    """Rebuild the recording tables from the work mentions' raw payloads."""
    session.execute(delete(RecordingWork))
    session.execute(delete(RecordingParticipant))
    session.execute(delete(Recording))

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
    for recording_id, (key, data) in enumerate(sorted(_group_recordings(session).items()), start=1):
        rows.add_recording(recording_id, key, data)

    for i in range(0, len(rows.recordings), INSERT_BATCH):
        session.execute(insert(Recording), rows.recordings[i : i + INSERT_BATCH])
    for i in range(0, len(rows.participants), INSERT_BATCH):
        session.execute(insert(RecordingParticipant), rows.participants[i : i + INSERT_BATCH])
    for i in range(0, len(rows.works), INSERT_BATCH):
        session.execute(insert(RecordingWork), rows.works[i : i + INSERT_BATCH])
    session.commit()

    return DeriveRecordingsStats(
        recordings=len(rows.recordings),
        participant_links=rows.participant_links,
        unresolved_participant_names=len(rows.unresolved_names),
    )
