"""Gold, read out as the node and relationship rows of a property graph.

Nothing here talks to Neo4j: each function walks one slice of the gold
database and yields plain dicts, grouped under the label (or relationship
type and endpoint labels) they belong to. ``load`` batches them into Cypher.
The big tables (works, the event ↔ work links) are streamed, never held whole.

The graph is the event graph, not a summary of it. Concerts, recordings and
works are nodes of their own, so "how strongly are these two connected" is a
query over paths — performer → event → work → composer — rather than a weight
fixed at export time.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from composer_gold import entity_sources, literal_claims, object_claims
from composer_models import (
    Concert,
    ConcertParticipant,
    ConcertWork,
    Entity,
    RawWorkMention,
    Recording,
    RecordingParticipant,
    RecordingWork,
    Source,
    Work,
    WorkIdentifier,
    WorkLink,
)
from composer_models.normalize import wikidata_id
from sqlalchemy import func, select
from sqlalchemy.orm import Session

Row = dict[str, Any]

# Rows fetched per round trip when streaming the large tables.
_YIELD_PER = 10_000

# Entity kinds and the node label each becomes. ``work`` entities are left out:
# gold's real works are the ``works`` table (the :Work nodes), and the handful
# of work entities only exist as the object of ``composed`` claims.
ENTITY_LABELS: dict[str, str] = {
    "person": "Person",
    "ensemble": "Ensemble",
    "place": "Place",
    "genre": "Genre",
    "movement": "Movement",
    "profession": "Profession",
    "period": "Period",
}

EVENT_LABELS = ("Concert", "Recording")
NODE_LABELS = (*ENTITY_LABELS.values(), "Work", *EVENT_LABELS)

# Object claims that become relationships. ``composed`` is not one: the works
# table says who composed what for every work, the claim for a few dozen.
CLAIM_TYPES: dict[str, str] = {
    "born_in": "BORN_IN",
    "died_in": "DIED_IN",
    "citizen_of": "CITIZEN_OF",
    "has_genre": "HAS_GENRE",
    "in_movement": "IN_MOVEMENT",
    "has_profession": "HAS_PROFESSION",
    "associated_period": "ASSOCIATED_PERIOD",
    # Genre → genre and movement → movement, from Wikidata's P279: (a)-[:SUBCLASS_OF]->(b)
    # means a is a kind of b, so a genre and everything below it is
    # (g)<-[:SUBCLASS_OF*0..]-(sub).
    "subclass_of": "SUBCLASS_OF",
    # Person ↔ person, from Wikidata. Read as subject → object: (a)-[:STUDENT_OF]->(b)
    # means a studied with b; (a)-[:CHILD_OF]->(b) means b is a's parent.
    "student_of": "STUDENT_OF",
    "teacher_of": "TEACHER_OF",
    "influenced_by": "INFLUENCED_BY",
    "child_of": "CHILD_OF",
    "parent_of": "PARENT_OF",
    "spouse_of": "SPOUSE_OF",
    "sibling_of": "SIBLING_OF",
}

# Literal claims carried onto entity nodes. Single-valued ones take the value
# the most sources back; counts become integers so Cypher can order by them;
# list-valued ones keep every distinct value. Bookkeeping claims
# (statement_count, mentioned_in, ...) are left out.
_LITERAL_SINGLE = ("born_on", "died_on", "first_season", "last_season", "musicbrainz_id")
_LITERAL_COUNTS = ("sitelink_count", "work_count", "program_count")
_LITERAL_LISTS = ("also_known_as", "performs_as", "has_function")


@dataclass(frozen=True)
class RelGroup:
    """Relationships of one type between one pair of node labels — the unit
    a single ``UNWIND ... MATCH ... CREATE`` statement can load."""

    type: str
    start: str
    end: str


@dataclass
class Skipped:
    """Gold rows that had nowhere to go in the graph, counted for the stats."""

    unmatched_mentions: int = 0
    orphan_works: int = 0
    other: dict[str, int] = field(default_factory=dict)

    def add(self, reason: str, count: int = 1) -> None:
        self.other[reason] = self.other.get(reason, 0) + count


def _clean(row: Row) -> Row:
    """Drop the nulls: Neo4j has no null properties, an absent key is the same."""
    return {key: value for key, value in row.items() if value is not None and value != []}


def _rel(start: str, end: str, **props: Any) -> Row:
    """A relationship row: the endpoint ids, and the properties to set on it."""
    return {"from": start, "to": end, "props": _clean(props)}


def _distinct(values: list[str]) -> list[str]:
    """Values as reported, minus the ones that differ only in case."""
    seen: set[str] = set()
    distinct: list[str] = []
    for value in values:
        if value.casefold() not in seen:
            seen.add(value.casefold())
            distinct.append(value)
    return distinct


def _literal_properties(literals: dict[str, list[str]]) -> Row:
    props: Row = {}
    for predicate in _LITERAL_SINGLE:
        if values := literals.get(predicate):
            props[predicate] = values[0]
    for predicate in _LITERAL_COUNTS:
        if (values := literals.get(predicate)) and values[0].isdigit():
            props[predicate] = int(values[0])
    for predicate in _LITERAL_LISTS:
        if values := literals.get(predicate):
            props[predicate] = _distinct(values)
    return props


# --- nodes -----------------------------------------------------------------


def entity_nodes(session: Session) -> Iterator[tuple[str, Row]]:
    """(label, row) for every gold entity of a kind the graph models.

    ``wikidata_id`` is the QID the entity's dedup key carries, when it carries
    one: the stable handle to match a node on, since labels follow Wikidata's
    renames."""
    literals = literal_claims(session, (*_LITERAL_SINGLE, *_LITERAL_COUNTS, *_LITERAL_LISTS))
    provenance = entity_sources(session)
    rows = session.execute(
        select(Entity.id, Entity.kind, Entity.label, Entity.dedup_key).order_by(Entity.kind, Entity.id)
    )
    for entity_id, kind, label, key in rows:
        node_label = ENTITY_LABELS.get(kind)
        if node_label is None:
            continue
        sources, url = provenance.get(entity_id, ([], None))
        yield (
            node_label,
            _clean(
                {
                    "id": str(entity_id),
                    "label": label,
                    "wikidata_id": wikidata_id(key),
                    "sources": sources,
                    "url": url,
                    **_literal_properties(literals.get(entity_id, {})),
                }
            ),
        )


def _work_identifiers(session: Session) -> dict[Any, list[tuple[str, str]]]:
    found: dict[Any, set[tuple[str, str]]] = {}
    for work_id, scheme, value in session.execute(
        select(WorkIdentifier.work_id, WorkIdentifier.scheme, WorkIdentifier.value)
    ):
        found.setdefault(work_id, set()).add((scheme, value))
    return {work_id: sorted(pairs) for work_id, pairs in found.items()}


def work_nodes(session: Session) -> Iterator[Row]:
    """Work nodes. ``identifiers`` is a list of "scheme value" strings ("BWV
    1048", "wikidata Q1") -- Neo4j properties can't hold maps -- with the
    Wikidata id also on ``wikidata_id`` for lookups."""
    identifiers = _work_identifiers(session)
    rows = session.execute(
        select(
            Work.id,
            Work.canonical_title,
            Work.work_type,
            Work.opus_number,
            Work.catalogue_prefix,
            Work.catalogue_number,
            Work.musical_key,
            Work.number,
            Work.premiere_date,
        )
        .order_by(Work.id)
        .execution_options(yield_per=_YIELD_PER)
    )
    for work_id, title, work_type, opus, cat_prefix, cat_number, key, number, premiere in rows:
        pairs = identifiers.get(work_id, [])
        yield _clean(
            {
                "id": str(work_id),
                "title": title,
                "work_type": work_type,
                "opus_number": opus,
                "catalogue_prefix": cat_prefix,
                "catalogue_number": cat_number,
                "musical_key": key,
                "number": number,
                "premiere_date": premiere,
                "wikidata_id": next((value for scheme, value in pairs if scheme == "wikidata"), None),
                "identifiers": [f"{scheme} {value}" for scheme, value in pairs],
            }
        )


def concert_nodes(session: Session) -> Iterator[Row]:
    rows = session.execute(
        select(
            Concert.id,
            Concert.date,
            Concert.season,
            Concert.event_type,
            Concert.venue,
            Concert.url,
            Source.name,
        )
        .join(Source, Source.id == Concert.source_id)
        .order_by(Concert.id)
        .execution_options(yield_per=_YIELD_PER)
    )
    for concert_id, date, season, event_type, venue, url, source in rows:
        yield _clean(
            {
                "id": str(concert_id),
                "date": date,
                "season": season,
                "event_type": event_type,
                "venue": venue,
                "url": url,
                "source": source,
            }
        )


def recording_nodes(session: Session) -> Iterator[Row]:
    rows = session.execute(
        select(
            Recording.id,
            Recording.title,
            Recording.release_date,
            Recording.label,
            Recording.catalogue_number,
            Recording.format,
            Recording.url,
            Source.name,
        )
        .join(Source, Source.id == Recording.source_id)
        .order_by(Recording.id)
        .execution_options(yield_per=_YIELD_PER)
    )
    for recording_id, title, release_date, label, catalogue_number, format_, url, source in rows:
        yield _clean(
            {
                "id": str(recording_id),
                "title": title,
                "release_date": release_date,
                "label": label,
                "catalogue_number": catalogue_number,
                "format": format_,
                "url": url,
                "source": source,
            }
        )


# --- relationships ---------------------------------------------------------


def composed_by(session: Session, skipped: Skipped) -> Iterator[tuple[RelGroup, Row]]:
    """(Work)-[:COMPOSED_BY]->(composer), for composers kept in gold.

    Gold prunes entities but keeps every work, so a work can point at a
    composer that is no longer there; those works stay unattached.
    """
    skipped.orphan_works += (
        session.scalar(
            select(func.count())
            .select_from(Work)
            .outerjoin(Entity, Entity.id == Work.composer_entity_id)
            .where(Entity.id.is_(None))
        )
        or 0
    )
    rows = session.execute(
        select(Work.id, Entity.id, Entity.kind)
        .join(Entity, Entity.id == Work.composer_entity_id)
        .order_by(Work.id)
        .execution_options(yield_per=_YIELD_PER)
    )
    for work_id, composer_id, kind in rows:
        if (label := ENTITY_LABELS.get(kind)) is None:
            skipped.add(f"composer of kind {kind}")
            continue
        yield RelGroup("COMPOSED_BY", "Work", label), _rel(str(work_id), str(composer_id))


def work_links(session: Session, skipped: Skipped) -> Iterator[tuple[RelGroup, Row]]:
    """(Work)-[:PART_OF|ARRANGEMENT_OF|VERSION_OF {method}]->(Work): a part,
    arrangement or version pointing at the work it derives from."""
    rows = session.execute(
        select(WorkLink.work_id, WorkLink.relation, WorkLink.target_work_id, WorkLink.method)
        .order_by(WorkLink.work_id, WorkLink.relation, WorkLink.target_work_id)
        .execution_options(yield_per=_YIELD_PER)
    )
    for work_id, relation, target_id, method in rows:
        yield RelGroup(relation.upper(), "Work", "Work"), _rel(str(work_id), str(target_id), method=method)


def performed_in(session: Session, skipped: Skipped) -> Iterator[tuple[RelGroup, Row]]:
    """(Person|Ensemble)-[:PERFORMED_IN {role, discipline}]->(Concert|Recording).

    One relationship per credit: a soloist who also conducted the same night
    gets two, one per role.
    """
    joins = (
        ("Concert", ConcertParticipant, ConcertParticipant.concert_id),
        ("Recording", RecordingParticipant, RecordingParticipant.recording_id),
    )
    for event_label, participant, event_fk in joins:
        rows = session.execute(
            select(Entity.id, Entity.kind, event_fk, participant.role, participant.discipline)
            .join(Entity, Entity.id == participant.entity_id)
            .order_by(event_fk, participant.id)
            .execution_options(yield_per=_YIELD_PER)
        )
        for entity_id, kind, event_id, role, discipline in rows:
            if (label := ENTITY_LABELS.get(kind)) is None:
                skipped.add(f"performer of kind {kind}")
                continue
            yield (
                RelGroup("PERFORMED_IN", label, event_label),
                _rel(str(entity_id), str(event_id), role=role, discipline=discipline),
            )


def programmed(session: Session, skipped: Skipped) -> Iterator[tuple[RelGroup, Row]]:
    """(Concert|Recording)-[:PROGRAMMED {times}]->(Work).

    Through the mention each event row points at, for mentions that resolved
    to a work; ``times`` counts a work played more than once at one event.
    """
    joins = (
        ("Concert", ConcertWork, ConcertWork.concert_id),
        ("Recording", RecordingWork, RecordingWork.recording_id),
    )
    for event_label, event_work, event_fk in joins:
        skipped.unmatched_mentions += (
            session.scalar(
                select(func.count())
                .select_from(event_work)
                .join(RawWorkMention, RawWorkMention.id == event_work.mention_id)
                .where(RawWorkMention.work_id.is_(None))
            )
            or 0
        )
        rows = session.execute(
            select(event_fk, RawWorkMention.work_id, func.count())
            .join(RawWorkMention, RawWorkMention.id == event_work.mention_id)
            .where(RawWorkMention.work_id.is_not(None))
            .group_by(event_fk, RawWorkMention.work_id)
            .order_by(event_fk, RawWorkMention.work_id)
            .execution_options(yield_per=_YIELD_PER)
        )
        for event_id, work_id, times in rows:
            assert work_id is not None  # guarded by the WHERE above
            yield RelGroup("PROGRAMMED", event_label, "Work"), _rel(str(event_id), str(work_id), times=times)


def claim_relationships(session: Session, skipped: Skipped) -> Iterator[tuple[RelGroup, Row]]:
    """The object claims (born in, has genre, ...) with the sources asserting each."""
    kinds = dict(session.execute(select(Entity.id, Entity.kind)).all())
    for (subject_id, predicate, object_id), sources in object_claims(session).items():
        rel_type = CLAIM_TYPES.get(predicate)
        start = ENTITY_LABELS.get(kinds.get(subject_id, ""))
        end = ENTITY_LABELS.get(kinds.get(object_id, ""))
        if rel_type is None or start is None or end is None:
            skipped.add(f"claim {predicate}")
            continue
        yield RelGroup(rel_type, start, end), _rel(str(subject_id), str(object_id), sources=sources)
