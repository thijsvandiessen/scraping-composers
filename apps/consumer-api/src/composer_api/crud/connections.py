"""One entity's neighbours in the gold graph, budgeted so a page can draw them.

The edges are the ones :mod:`composer_gold.kumu` exports full-graph, asked one
entity at a time instead: a performer is joined to the composers it programmed
through its concerts' (or recordings') works, and object-valued claims
(``born_in``, ``student_of``, ``has_genre``, ...) hang the biographical context
off both ends.

Every knob here exists because the raw neighbourhood is unusable. A busy
conductor touches hundreds of composers and thousands of colleagues, and the
heaviest edges are the least interesting ones — everybody performs Beethoven,
so "performs Beethoven" says nothing about anybody. Four budgets cut that down
to something a reader (and a layout) can take in:

``min_weight``
    Drops one-off co-occurrences, which are mostly matcher noise.
``per_relation``
    A separate budget per relation, so the performance edges — always the
    numerous ones — cannot crowd the handful of biographical edges out of the
    graph entirely.
``limit``
    The overall cap, sized for a drawing rather than for completeness.
``rank``
    ``weight`` ranks by raw co-occurrence and surfaces the famous. ``affinity``
    (the default) divides by the square root of how much the neighbour is
    performed or referenced overall, which demotes the hubs everyone connects
    to and lets a pairing that is *distinctive* — Karajan and Strauss rather
    than Karajan and Beethoven — come out on top. It is a ranking heuristic,
    deliberately a plain one; ``weight`` is always there to check it against.
"""

import math
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from composer_models import (
    Claim,
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
)
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..deps import GraphBudget
from ..errors import NotFoundError
from ..schemas import ConnectionOut, ConnectionsOut

# Relations, in the order the graph should read them.
PERFORMED = "performed"  # this entity performed that composer's music
PERFORMED_BY = "performed_by"  # that performer played this composer's music
APPEARED_WITH = "appeared_with"  # shared a concert or a recording session

PERFORMANCE_RELATIONS = frozenset({PERFORMED, PERFORMED_BY, APPEARED_WITH})

# Claim predicates that would connect nearly everyone to a handful of nodes.
# "has_profession" makes the profession entity ("composer") the biggest hub in
# the dataset while saying nothing about any particular pair.
HUB_PREDICATES = frozenset({"has_profession"})

# Proof snippets carried per edge. Three is enough to show the reader what the
# edge is made of without turning the payload into a concert listing.
VIA_CAP = 3

# The participant/work pair of each event kind, and the foreign keys that tie a
# participant row to the work rows of the same event. Passed as explicit column
# arguments (rather than reached for off the class) so the two event families
# can share one query body.
_EVENT_JOINS = (
    (ConcertParticipant, ConcertWork, ConcertParticipant.concert_id, ConcertWork.concert_id),
    (RecordingParticipant, RecordingWork, RecordingParticipant.recording_id, RecordingWork.recording_id),
)


@dataclass
class _Edge:
    """One neighbour, before ranking and before its proof is attached."""

    entity_id: uuid.UUID
    relation: str
    weight: int = 0
    direction: str = "out"
    roles: set[str] = field(default_factory=set)
    via: list[str] = field(default_factory=list)
    score: float = 0.0


def _performed_edges(db: Session, entity_id: uuid.UUID) -> list[_Edge]:
    """Composers whose music this entity performed, weighted by performances."""
    weights: Counter[uuid.UUID] = Counter()
    roles: defaultdict[uuid.UUID, set[str]] = defaultdict(set)
    for participant, event_work, participant_fk, event_work_fk in _EVENT_JOINS:
        rows = db.execute(
            select(Work.composer_entity_id, participant.role, func.count())
            .select_from(participant)
            .join(event_work, event_work_fk == participant_fk)
            .join(RawWorkMention, RawWorkMention.id == event_work.mention_id)
            .join(Work, Work.id == RawWorkMention.work_id)
            # Gold keeps every work but prunes entities, so a work can point at
            # a composer that did not survive promotion; the join drops those.
            .join(Entity, Entity.id == Work.composer_entity_id)
            .where(participant.entity_id == entity_id, Work.composer_entity_id != entity_id)
            .group_by(Work.composer_entity_id, participant.role)
        ).tuples()
        for composer_id, role, count in rows:
            assert composer_id is not None  # guarded by the join on Entity
            weights[composer_id] += count
            roles[composer_id].add(role)
    return [
        _Edge(entity_id=cid, relation=PERFORMED, weight=weight, roles=roles[cid])
        for cid, weight in weights.items()
    ]


def _performed_by_edges(db: Session, entity_id: uuid.UUID) -> list[_Edge]:
    """Performers and ensembles that played this composer's music."""
    weights: Counter[uuid.UUID] = Counter()
    roles: defaultdict[uuid.UUID, set[str]] = defaultdict(set)
    for participant, event_work, participant_fk, event_work_fk in _EVENT_JOINS:
        rows = db.execute(
            select(participant.entity_id, participant.role, func.count())
            .select_from(Work)
            .join(RawWorkMention, RawWorkMention.work_id == Work.id)
            .join(event_work, event_work.mention_id == RawWorkMention.id)
            .join(participant, participant_fk == event_work_fk)
            .where(
                Work.composer_entity_id == entity_id,
                participant.entity_id.is_not(None),
                participant.entity_id != entity_id,
            )
            .group_by(participant.entity_id, participant.role)
        ).tuples()
        for performer_id, role, count in rows:
            assert performer_id is not None  # guarded by the WHERE above
            weights[performer_id] += count
            roles[performer_id].add(role)
    return [
        _Edge(entity_id=pid, relation=PERFORMED_BY, weight=weight, direction="in", roles=roles[pid])
        for pid, weight in weights.items()
    ]


def _appeared_with_edges(db: Session, entity_id: uuid.UUID) -> list[_Edge]:
    """Everyone billed alongside this entity, weighted by shared events.

    Two passes rather than a self-join: the events this entity appears at, then
    everyone else appearing at those. Both hit ``ix_*_participants_entity`` and
    the event-id index, and it keeps the query free of an alias.
    """
    weights: Counter[uuid.UUID] = Counter()
    roles: defaultdict[uuid.UUID, set[str]] = defaultdict(set)
    for participant, _event_work, participant_fk, _event_work_fk in _EVENT_JOINS:
        events = select(participant_fk).where(participant.entity_id == entity_id).scalar_subquery()
        rows = db.execute(
            select(participant.entity_id, participant.role, func.count(func.distinct(participant_fk)))
            .where(
                participant_fk.in_(events),
                participant.entity_id.is_not(None),
                participant.entity_id != entity_id,
            )
            .group_by(participant.entity_id, participant.role)
        ).tuples()
        for other_id, role, count in rows:
            assert other_id is not None  # guarded by the WHERE above
            weights[other_id] += count
            roles[other_id].add(role)
    return [
        _Edge(entity_id=oid, relation=APPEARED_WITH, weight=weight, roles=roles[oid])
        for oid, weight in weights.items()
    ]


def _claim_edges(db: Session, entity_id: uuid.UUID) -> list[_Edge]:
    """Object-valued claims either way, weighted by how many sources assert them.

    A claim asserted by three sources outranks one asserted by a single
    scraper, which is the only corroboration signal the claims table carries.
    """
    edges: dict[tuple[uuid.UUID, str, str], _Edge] = {}
    directions = (
        ("out", Claim.subject_id == entity_id, Claim.object_id),
        ("in", Claim.object_id == entity_id, Claim.subject_id),
    )
    for direction, subject_filter, other_column in directions:
        rows = db.execute(
            select(other_column, Claim.predicate, Source.name)
            .join(Source, Source.id == Claim.source_id)
            .where(subject_filter, Claim.object_id.is_not(None), other_column != entity_id)
            .distinct()
        ).tuples()
        for other_id, predicate, source_name in rows:
            if other_id is None or predicate in HUB_PREDICATES:
                continue
            key = (other_id, predicate, direction)
            edge = edges.get(key)
            if edge is None:
                edge = _Edge(entity_id=other_id, relation=predicate, direction=direction)
                edges[key] = edge
            edge.weight += 1
            if len(edge.via) < VIA_CAP and source_name not in edge.via:
                edge.via.append(source_name)
    return list(edges.values())


def _performance_prominence(db: Session, composer_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """How often each composer is performed at all — the hub discount's denominator."""
    totals: Counter[uuid.UUID] = Counter()
    if not composer_ids:
        return totals
    for _participant, event_work, _participant_fk, _event_work_fk in _EVENT_JOINS:
        rows = db.execute(
            select(Work.composer_entity_id, func.count())
            .select_from(event_work)
            .join(RawWorkMention, RawWorkMention.id == event_work.mention_id)
            .join(Work, Work.id == RawWorkMention.work_id)
            .where(Work.composer_entity_id.in_(composer_ids))
            .group_by(Work.composer_entity_id)
        ).tuples()
        for composer_id, count in rows:
            assert composer_id is not None  # guarded by the IN above
            totals[composer_id] += count
    return totals


def _appearance_prominence(db: Session, performer_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """How many events each performer appears at at all."""
    totals: Counter[uuid.UUID] = Counter()
    if not performer_ids:
        return totals
    for participant, _event_work, participant_fk, _event_work_fk in _EVENT_JOINS:
        rows = db.execute(
            select(participant.entity_id, func.count(func.distinct(participant_fk)))
            .where(participant.entity_id.in_(performer_ids))
            .group_by(participant.entity_id)
        ).tuples()
        for performer_id, count in rows:
            assert performer_id is not None  # guarded by the IN above
            totals[performer_id] += count
    return totals


def _claim_prominence(db: Session, entity_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """How many distinct entities each claim neighbour is attached to.

    Vienna is a weak signal precisely because half the dataset was born there.
    """
    if not entity_ids:
        return {}
    rows = db.execute(
        select(Claim.object_id, func.count(func.distinct(Claim.subject_id)))
        .where(Claim.object_id.in_(entity_ids))
        .group_by(Claim.object_id)
    ).tuples()
    return {object_id: count for object_id, count in rows if object_id is not None}


def _prominence(db: Session, edges: list[_Edge]) -> dict[uuid.UUID, int]:
    """The denominator for every edge, looked up per relation family."""
    performed = [e.entity_id for e in edges if e.relation == PERFORMED]
    performers = [e.entity_id for e in edges if e.relation in (PERFORMED_BY, APPEARED_WITH)]
    claims = [e.entity_id for e in edges if e.relation not in PERFORMANCE_RELATIONS]
    return {
        **_performance_prominence(db, performed),
        **_appearance_prominence(db, performers),
        **_claim_prominence(db, claims),
    }


def _score(edge: _Edge, prominence: dict[uuid.UUID, int], rank: str) -> float:
    """Rank key: raw weight, or weight discounted by the neighbour's reach.

    The square root is the usual soft discount — it pulls the hubs down without
    handing the top of the list to every one-off pairing, which dividing by the
    raw total would.
    """
    if rank == "weight":
        return float(edge.weight)
    return edge.weight / math.sqrt(max(prominence.get(edge.entity_id, 1), 1))


def _budgeted(edges: list[_Edge], per_relation: int, limit: int) -> list[_Edge]:
    """Top ``per_relation`` of each relation, then the best ``limit`` overall.

    ``edges`` arrives sorted best-first, so one pass gives each relation its own
    budget: a conductor's thousand performance edges cannot squeeze out the two
    claims that say where he was born, because they are spending different
    allowances.
    """
    taken: Counter[str] = Counter()
    kept: list[_Edge] = []
    for edge in edges:
        if taken[edge.relation] >= per_relation:
            continue
        taken[edge.relation] += 1
        kept.append(edge)
        if len(kept) >= limit:
            break
    return kept


def _attach_performance_via(db: Session, entity_id: uuid.UUID, edges: list[_Edge]) -> None:
    """Hang a few real performances off each performance edge, as its proof.

    Only the edges that survived the budget are asked about, so this is one
    extra query per event kind over at most ``limit`` neighbours.
    """
    performed = {e.entity_id: e for e in edges if e.relation == PERFORMED}
    performed_by = {e.entity_id: e for e in edges if e.relation == PERFORMED_BY}
    if not performed and not performed_by:
        return
    ids = list(performed.keys() | performed_by.keys())
    # The two event kinds date themselves differently (a concert happened on a
    # date, a recording was released on one), so the date column travels with
    # the join rather than being reached for off the mapped class.
    joins = (
        (
            ConcertParticipant,
            ConcertWork,
            Concert,
            ConcertParticipant.concert_id,
            ConcertWork.concert_id,
            Concert.date,
        ),
        (
            RecordingParticipant,
            RecordingWork,
            Recording,
            RecordingParticipant.recording_id,
            RecordingWork.recording_id,
            Recording.release_date,
        ),
    )
    for participant, event_work, event, participant_fk, event_work_fk, date_column in joins:
        # One query serves both directions: the rows are (performer, composer,
        # title, date) tuples, and which side is the neighbour depends only on
        # which end the focused entity sits at.
        rows = db.execute(
            select(participant.entity_id, Work.composer_entity_id, RawWorkMention.raw_title, date_column)
            .select_from(participant)
            .join(event, event.id == participant_fk)
            .join(event_work, event_work_fk == participant_fk)
            .join(RawWorkMention, RawWorkMention.id == event_work.mention_id)
            .join(Work, Work.id == RawWorkMention.work_id)
            .where(
                or_(
                    and_(participant.entity_id == entity_id, Work.composer_entity_id.in_(ids)),
                    and_(Work.composer_entity_id == entity_id, participant.entity_id.in_(ids)),
                )
            )
            .order_by(date_column.desc().nulls_last())
        ).tuples()
        for performer_id, composer_id, title, date in rows:
            # Both sides are nullable columns; the WHERE above only guarantees
            # that one end is the focused entity.
            if performer_id is None or composer_id is None:
                continue
            edge = performed.get(composer_id) if performer_id == entity_id else performed_by.get(performer_id)
            if edge is None or len(edge.via) >= VIA_CAP:
                continue
            snippet = f"{title} ({date[:4]})" if date else title
            if snippet not in edge.via:
                edge.via.append(snippet)


def entity_connections(
    db: Session, entity_id: uuid.UUID, budget: GraphBudget | None = None
) -> ConnectionsOut:
    """The strongest neighbours of one entity, ready to draw.

    ``total`` reports how many edges cleared ``min_weight`` before the budgets
    were applied, so a caller can tell a sparse neighbourhood from a truncated
    one.
    """
    budget = budget or GraphBudget()
    entity = db.get(Entity, entity_id)
    if entity is None:
        raise NotFoundError("entity not found")

    edges = [
        *_performed_edges(db, entity_id),
        *_performed_by_edges(db, entity_id),
        *_appeared_with_edges(db, entity_id),
        *_claim_edges(db, entity_id),
    ]
    edges = [e for e in edges if e.weight >= budget.min_weight]
    if budget.relation is not None:
        edges = [e for e in edges if e.relation == budget.relation]
    total = len(edges)

    prominence = _prominence(db, edges)
    for edge in edges:
        edge.score = _score(edge, prominence, budget.rank)
    # Ties broken on the id so a page reloads to the same picture.
    edges.sort(key=lambda e: (-e.score, -e.weight, str(e.entity_id)))
    kept = _budgeted(edges, budget.per_relation, budget.limit)
    _attach_performance_via(db, entity_id, kept)

    # One lookup for the labels and kinds of everything that survived, rather
    # than an ORM load per edge.
    named = {
        row_id: (label, kind)
        for row_id, label, kind in db.execute(
            select(Entity.id, Entity.label, Entity.kind).where(Entity.id.in_([e.entity_id for e in kept]))
        )
        .tuples()
        .all()
    }

    return ConnectionsOut(
        entity_id=entity.id,
        label=entity.label,
        kind=entity.kind,
        items=[
            ConnectionOut(
                entity_id=edge.entity_id,
                label=named[edge.entity_id][0],
                kind=named[edge.entity_id][1],
                relation=edge.relation,
                direction=edge.direction,
                weight=edge.weight,
                score=round(edge.score, 4),
                roles=sorted(edge.roles),
                via=edge.via,
            )
            for edge in kept
        ],
        total=total,
        limit=budget.limit,
        rank=budget.rank,
    )
