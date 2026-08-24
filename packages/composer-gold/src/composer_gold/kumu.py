"""Export the gold database as a Kumu blueprint (``{"elements": [...], "connections": [...]}``).

Kumu maps one network at a time, and gold is far too large to hand it whole
(7k entities, 20k performer/composer pairs, 138k works). So the export is a
*slice*: the ``limit`` most-active performers and ensembles, the composers
whose music they programmed, and the biographical context hanging off both.

Two kinds of edge make it in:

- **performances** — performer/ensemble → composer, derived by walking a
  concert's (or recording's) participants and its works back to each work's
  composer. Weighted by how many performances of that composer's music the
  pairing accounts for, so Kumu can size or filter by it.
- **claims** — the object-valued rows of ``claims`` (``has_profession``,
  ``born_in``, ``citizen_of``, ``has_genre``, ``studied_with``, ...), collapsed
  across the sources that assert them.

Literal claims (``born_on``, ``program_count``, ...) are not edges; they become
fields on the element, which is where Kumu wants them for styling and filtering.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from composer_models import (
    Claim,
    ConcertParticipant,
    ConcertWork,
    Entity,
    EntityRecord,
    RawWorkMention,
    RecordingParticipant,
    RecordingWork,
    Source,
    Work,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# Top-N performers to seed the slice with. 500 keeps the blueprint around a
# thousand elements, which Kumu opens comfortably; the whole 5.6k-performer
# graph does not.
DEFAULT_PERFORMER_LIMIT = 500

# Literal claim predicates worth carrying onto the element, and the field name
# Kumu should show them under. Anything not listed here is dropped: gold keeps
# bookkeeping claims (statement_count, mentioned_in) that would only clutter
# the profile panel.
_LITERAL_FIELDS: dict[str, str] = {
    "born_on": "Born",
    "died_on": "Died",
    "program_count": "Programs",
    "first_season": "First season",
    "last_season": "Last season",
    "work_count": "Works (Wikidata)",
    "sitelink_count": "Wikipedia sitelinks",
}

# Of those, the ones whose value is a count. Claim values are all text, and a
# map that sizes or filters by "programs" needs a number, not "131". Dates stay
# text — a birth date recorded as the bare year would otherwise turn into an
# integer and stop reading as a date.
_COUNT_PREDICATES = frozenset({"program_count", "work_count", "sitelink_count"})

# Literal predicates that are multi-valued per entity ("violin", "piano") and
# read better joined than picked from.
_LITERAL_LISTS: dict[str, str] = {
    "performs_as": "Instruments",
    "has_function": "Functions",
}

KumuObject = dict[str, Any]


@dataclass(frozen=True)
class KumuConfig:
    """What to put in the blueprint.

    ``performer_limit`` is the compaction knob: performers and ensembles ranked
    by concert + recording appearances, cut at N (0 = every one of them).
    Composers are then pulled in by the edges that survive ``min_weight``, so a
    bigger limit widens both ends of the network at once.
    """

    performer_limit: int = DEFAULT_PERFORMER_LIMIT
    min_weight: int = 1  # drop performer→composer edges below this many performances
    performances: bool = True
    claims: bool = True


@dataclass(frozen=True)
class ExportStats:
    elements: int = 0
    connections: int = 0
    performers: int = 0
    composers: int = 0
    performance_edges: int = 0
    claim_edges: int = 0

    def __str__(self) -> str:
        return (
            f"{self.elements} elements, {self.connections} connections "
            f"({self.performance_edges} performances, {self.claim_edges} claims) "
            f"from {self.performers} performers and {self.composers} composers"
        )


@dataclass(frozen=True)
class Blueprint:
    """A Kumu blueprint: the two arrays Kumu's JSON import expects."""

    elements: list[KumuObject]
    connections: list[KumuObject]
    stats: ExportStats

    def to_dict(self) -> dict[str, list[KumuObject]]:
        return {"elements": self.elements, "connections": self.connections}

    def write(self, path: str | Path) -> None:
        target = Path(path)
        target.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --- gathering -------------------------------------------------------------


@dataclass(frozen=True)
class _Appearances:
    """Per-entity credit counts, and the roles those credits were under."""

    concerts: dict[uuid.UUID, int]
    recordings: dict[uuid.UUID, int]
    roles: dict[uuid.UUID, set[str]]

    def total(self, entity_id: uuid.UUID) -> int:
        return self.concerts.get(entity_id, 0) + self.recordings.get(entity_id, 0)


def _appearances(session: Session) -> _Appearances:
    concerts: dict[uuid.UUID, int] = {}
    recordings: dict[uuid.UUID, int] = {}
    roles: dict[uuid.UUID, set[str]] = defaultdict(set)
    for table, counts in ((ConcertParticipant, concerts), (RecordingParticipant, recordings)):
        rows = session.execute(
            select(table.entity_id, table.role, func.count())
            .where(table.entity_id.is_not(None))
            .group_by(table.entity_id, table.role)
        ).tuples()
        for entity_id, role, count in rows:
            assert entity_id is not None  # guarded by the WHERE above
            counts[entity_id] = counts.get(entity_id, 0) + count
            roles[entity_id].add(role)
    return _Appearances(concerts=concerts, recordings=recordings, roles=dict(roles))


def _performance_edges(session: Session) -> dict[tuple[uuid.UUID, uuid.UUID], int]:
    """(performer, composer) → number of performances linking them.

    Both halves of a concert row are joined through the concert: its
    participants on one side, the works on its programme (via the mention that
    resolved to a work) on the other. Recordings contribute the same way.
    """
    edges: Counter[tuple[uuid.UUID, uuid.UUID]] = Counter()
    joins = (
        (ConcertParticipant, ConcertWork, ConcertParticipant.concert_id, ConcertWork.concert_id),
        (RecordingParticipant, RecordingWork, RecordingParticipant.recording_id, RecordingWork.recording_id),
    )
    for participant, event_work, participant_fk, event_work_fk in joins:
        rows = session.execute(
            select(participant.entity_id, Work.composer_entity_id, func.count())
            .join(event_work, event_work_fk == participant_fk)
            .join(RawWorkMention, RawWorkMention.id == event_work.mention_id)
            .join(Work, Work.id == RawWorkMention.work_id)
            # Gold prunes entities but keeps every work, so a work can still
            # point at a composer that did not survive promotion.
            .join(Entity, Entity.id == Work.composer_entity_id)
            .where(participant.entity_id.is_not(None))
            .group_by(participant.entity_id, Work.composer_entity_id)
        ).tuples()
        for performer_id, composer_id, count in rows:
            assert performer_id is not None and composer_id is not None
            edges[(performer_id, composer_id)] += count
    return dict(edges)


def _object_claims(session: Session) -> dict[tuple[uuid.UUID, str, uuid.UUID], list[str]]:
    """(subject, predicate, object) → the source names asserting it."""
    rows = session.execute(
        select(Claim.subject_id, Claim.predicate, Claim.object_id, Source.name)
        .join(Source, Source.id == Claim.source_id)
        .where(Claim.object_id.is_not(None))
        .order_by(Claim.subject_id, Claim.predicate, Source.name)
    ).tuples()
    claims: dict[tuple[uuid.UUID, str, uuid.UUID], list[str]] = defaultdict(list)
    for subject_id, predicate, object_id, source_name in rows:
        assert object_id is not None
        sources = claims[(subject_id, predicate, object_id)]
        if source_name not in sources:
            sources.append(source_name)
    return dict(claims)


def _literal_claims(session: Session) -> dict[uuid.UUID, dict[str, list[str]]]:
    """subject → predicate → the distinct values sources gave it.

    Sources disagree (two birth dates for the same person is routine), so the
    values are kept in "most sources first" order and the caller picks.
    """
    wanted = set(_LITERAL_FIELDS) | set(_LITERAL_LISTS)
    rows = session.execute(
        select(Claim.subject_id, Claim.predicate, Claim.value, func.count())
        .where(Claim.object_id.is_(None), Claim.value.is_not(None), Claim.predicate.in_(wanted))
        .group_by(Claim.subject_id, Claim.predicate, Claim.value)
    ).tuples()
    tallies: dict[uuid.UUID, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    for subject_id, predicate, value, count in rows:
        assert value is not None
        tallies[subject_id][predicate][value] += count
    return {
        subject_id: {
            # Ties broken on the value itself so the export is reproducible.
            predicate: [value for value, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
            for predicate, counts in by_predicate.items()
        }
        for subject_id, by_predicate in tallies.items()
    }


def _entity_sources(session: Session) -> dict[uuid.UUID, tuple[list[str], str | None]]:
    """entity → (source names that reported it, a URL one of them gave)."""
    rows = session.execute(
        select(EntityRecord.entity_id, Source.name, EntityRecord.url)
        .join(Source, Source.id == EntityRecord.source_id)
        .where(EntityRecord.entity_id.is_not(None))
        .order_by(EntityRecord.entity_id, Source.name)
    ).tuples()
    names: dict[uuid.UUID, list[str]] = defaultdict(list)
    urls: dict[uuid.UUID, str] = {}
    for entity_id, source_name, url in rows:
        assert entity_id is not None
        if source_name not in names[entity_id]:
            names[entity_id].append(source_name)
        if url and entity_id not in urls:
            urls[entity_id] = url
    return {entity_id: (source_names, urls.get(entity_id)) for entity_id, source_names in names.items()}


# --- selection -------------------------------------------------------------


def _top_performers(appearances: _Appearances, limit: int) -> list[uuid.UUID]:
    """The most-credited performers and ensembles, biggest first.

    Sorted on (appearances, id) rather than appearances alone: the tail is full
    of ties, and an arbitrary cut through them would make two exports of the
    same database differ.
    """
    ranked = sorted(
        appearances.roles,
        key=lambda entity_id: (-appearances.total(entity_id), str(entity_id)),
    )
    return ranked if limit <= 0 else ranked[:limit]


# --- rendering -------------------------------------------------------------

_KIND_TYPES: dict[str, str] = {
    "person": "Person",
    "ensemble": "Ensemble",
    "place": "Place",
    "genre": "Genre",
    "profession": "Profession",
    "period": "Period",
    "movement": "Movement",
    "work": "Work",
    "event": "Event",
}

# Participant roles, in the order they read best on an element.
_ROLE_ORDER = ("composer", "conductor", "soloist", "performer", "ensemble")


def _year(date_value: str | None) -> str | None:
    """The year part of a claim's date string ("1918-08-25" → "1918")."""
    if not date_value:
        return None
    year = date_value.lstrip("+")[:4]
    return year if year.isdigit() else None


def _description(tags: list[str], fields: dict[str, Any]) -> str:
    """The one-liner Kumu shows under an element's name."""
    parts: list[str] = []
    if tags:
        roles = ", ".join(tags)
        parts.append(roles[0].upper() + roles[1:])
    lived = "–".join(filter(None, (_year(fields.get("Born")), _year(fields.get("Died")))))
    if lived:
        parts.append(lived)
    credits = [
        f"{fields[label]} {label.lower()}" for label in ("Concerts", "Recordings") if fields.get(label)
    ]
    if credits:
        parts.append(", ".join(credits))
    return " · ".join(parts)


def _distinct(values: list[str]) -> list[str]:
    """Values as reported, minus the ones that differ only in case: sources
    supply both "Piano" and "piano" for the same instrument."""
    seen: set[str] = set()
    distinct: list[str] = []
    for value in values:
        if value.casefold() not in seen:
            seen.add(value.casefold())
            distinct.append(value)
    return distinct


def _fields(
    entity_id: uuid.UUID, appearances: _Appearances, literals: dict[str, list[str]]
) -> dict[str, Any]:
    """The element's data fields: the literal claims worth keeping, plus how
    often the entity was credited."""
    fields: dict[str, Any] = {}
    for predicate, label in _LITERAL_FIELDS.items():
        # Sources disagree; _literal_claims already ranked them, so take the
        # value the most sources back.
        if values := literals.get(predicate):
            value = values[0]
            fields[label] = int(value) if predicate in _COUNT_PREDICATES and value.isdigit() else value
    for predicate, label in _LITERAL_LISTS.items():
        if values := literals.get(predicate):
            fields[label] = ", ".join(_distinct(values))
    if concerts := appearances.concerts.get(entity_id, 0):
        fields["Concerts"] = concerts
    if recordings := appearances.recordings.get(entity_id, 0):
        fields["Recordings"] = recordings
    return fields


def _tags(entity_id: uuid.UUID, appearances: _Appearances, is_composer: bool) -> list[str]:
    """The roles this entity turned up in — a Kumu tag each, so one person can
    be a composer *and* a conductor without the map having to choose."""
    roles = set(appearances.roles.get(entity_id, set()))
    if is_composer:
        roles.add("composer")
    return [role for role in _ROLE_ORDER if role in roles]


def _element(
    entity: Entity,
    appearances: _Appearances,
    literals: dict[str, list[str]],
    provenance: tuple[list[str], str | None],
    is_composer: bool,
) -> KumuObject:
    fields = _fields(entity.id, appearances, literals)
    tags = _tags(entity.id, appearances, is_composer)
    source_names, url = provenance
    element: KumuObject = {
        "id": str(entity.id),
        "label": entity.label,
        "type": _KIND_TYPES.get(entity.kind, entity.kind.title()),
    }
    if tags:
        element["tags"] = tags
    if description := _description(tags, fields):
        element["description"] = description
    element.update(fields)
    if source_names:
        element["Sources"] = ", ".join(source_names)
    if url:
        element["Source URL"] = url
    return element


def _connection(from_id: uuid.UUID, to_id: uuid.UUID, type_: str, **fields: Any) -> KumuObject:
    return {
        "from": str(from_id),
        "to": str(to_id),
        "type": type_,
        "direction": "directed",
        **fields,
    }


# --- the export ------------------------------------------------------------


def build_blueprint(session: Session, config: KumuConfig | None = None) -> Blueprint:
    """Build the Kumu blueprint for a gold session.

    Elements that end up with no connection at all are left out: a performer
    whose concerts never resolved to a composer, or an entity every one of
    whose claims pointed at something outside the slice, is noise on the map.
    """
    cfg = config or KumuConfig()
    appearances = _appearances(session)
    performers = _top_performers(appearances, cfg.performer_limit)
    selected = set(performers)

    connections: list[KumuObject] = []
    composers: set[uuid.UUID] = set()
    performance_edges = 0
    if cfg.performances:
        edges = _performance_edges(session)
        for (performer_id, composer_id), weight in sorted(
            edges.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))
        ):
            if performer_id not in selected or weight < cfg.min_weight:
                continue
            composers.add(composer_id)
            connections.append(
                _connection(performer_id, composer_id, "performed", **{"Performances": weight})
            )
            performance_edges += 1

    # Composers reached through the edges are part of the network too — and a
    # top performer may itself be a composer, which the tag on the element
    # will show.
    core = selected | composers
    claim_edges = 0
    peripheral: set[uuid.UUID] = set()
    if cfg.claims:
        for (subject_id, predicate, object_id), sources in sorted(
            _object_claims(session).items(), key=lambda kv: (str(kv[0][0]), kv[0][1], str(kv[0][2]))
        ):
            if subject_id not in core:
                continue
            peripheral.add(object_id)
            connections.append(
                _connection(
                    subject_id,
                    object_id,
                    predicate.replace("_", " "),
                    **{"Sources": ", ".join(sources)},
                )
            )
            claim_edges += 1

    connected = {conn["from"] for conn in connections} | {conn["to"] for conn in connections}
    literals = _literal_claims(session)
    provenance = _entity_sources(session)
    rows = session.scalars(
        select(Entity).where(Entity.id.in_(core | peripheral)).order_by(Entity.kind, Entity.label)
    ).all()
    elements = [
        _element(
            entity,
            appearances,
            literals.get(entity.id, {}),
            provenance.get(entity.id, ([], None)),
            is_composer=entity.id in composers,
        )
        for entity in rows
        if str(entity.id) in connected
    ]

    stats = ExportStats(
        elements=len(elements),
        connections=len(connections),
        performers=len(performers),
        composers=len(composers),
        performance_edges=performance_edges,
        claim_edges=claim_edges,
    )
    log.info("kumu blueprint: %s", stats)
    return Blueprint(elements=elements, connections=connections, stats=stats)


def export_kumu(session: Session, path: str | Path, config: KumuConfig | None = None) -> ExportStats:
    """Build the blueprint and write it to ``path`` as JSON."""
    blueprint = build_blueprint(session, config)
    blueprint.write(path)
    log.info("wrote %s", path)
    return blueprint.stats
