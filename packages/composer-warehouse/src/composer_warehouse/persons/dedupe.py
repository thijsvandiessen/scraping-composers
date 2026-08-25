"""The post-hoc person dedupe pass.

Blocks the person entities by surname, scores every pair in a block with the
trained Fellegi-Sunter model, and records the decision as a ``PersonMatch``
row: pairs above the auto cut-point are links, middling pairs are queued for
review, the rest are ignored. Re-running is idempotent — a pair that already
has a ``PersonMatch`` is skipped.

Scoring decides pairs; :mod:`cluster` decides *groups*. Once every pair is
recorded the pass rebuilds the whole partition from the surviving links and
writes one canonical per cluster, so ``Entity.canonical_entity_id`` is always a
single hop to a cluster canonical and never a link in a chain.

:mod:`constraints` gets a veto over that partition. Two records carrying
distinct Wikidata QIDs or distinct MusicBrainz ids are two people unless
corroboration says otherwise, and the constraint is enforced against the whole
cluster — so a merge is refused even when the score that proposed it came from
a third record.

Scores are posterior probabilities from :mod:`fellegi_sunter`, and the pass
builds term-frequency tables over the corpus before it starts, so sharing a
rare surname counts for far more than sharing a common one. Without that table
the model still runs but treats ``Smith`` and ``Sonnenfeld`` alike, which is
the failure #173 was filed about.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, cast

from composer_models import Entity, PersonMatch
from sqlalchemy import CursorResult, Delete, Update, delete, select, update
from sqlalchemy.orm import Session

from .cluster import Clustering, Edge, build_clusters
from .constraints import Constraints, authority_constraints
from .corpus import PersonRecord, build_corpus, candidate_pairs, load_records
from .extract import PersonName, parse_name
from .match import PersonScorer, classify, default_model

log = logging.getLogger(__name__)

COMMIT_BATCH = 1000

# Decisions a human made, which a re-run must never discard.
HUMAN_STATUSES = ("accepted", "rejected")


def _given_length(name: PersonName) -> int:
    """Total spelled-out length of the given names — so "Johann Sebastian"
    counts as fuller than the initials "J. S." (same token count)."""
    return sum(len(token) for token in name.given)


def _canonical_and_duplicate(a: PersonRecord, b: PersonRecord) -> tuple[PersonRecord, PersonRecord]:
    """The fuller name (most spelled-out given names; id tie-break) is canonical.

    This orients the ``PersonMatch`` row — the audit trail of what was scored
    against what. Which entity a cluster actually points at is decided later,
    once, by :func:`_cluster_canonical` over the whole membership.
    """
    la, lb = _given_length(a.name), _given_length(b.name)
    if la != lb:
        return (a, b) if la > lb else (b, a)
    return (a, b) if str(a.entity_id) < str(b.entity_id) else (b, a)


@dataclass
class _DedupeState:
    """Scorer, the pairs already decided, and progress counters for one pass."""

    session: Session
    scorer: PersonScorer
    decided: set[tuple[uuid.UUID, uuid.UUID]]
    auto: int = 0
    review: int = 0
    pending: int = 0


def _record_decision(
    state: _DedupeState, a: PersonRecord, b: PersonRecord, value: float, method: str
) -> None:
    canonical, duplicate = _canonical_and_duplicate(a, b)
    if (duplicate.entity_id, canonical.entity_id) in state.decided:
        return
    status = classify(value)
    state.session.add(
        PersonMatch(
            entity_id=duplicate.entity_id,
            canonical_entity_id=canonical.entity_id,
            score=value,
            method=method,
            status=status,
        )
    )
    state.decided.add((duplicate.entity_id, canonical.entity_id))
    if status == "auto_linked":
        state.auto += 1
    else:
        state.review += 1
    state.pending += 1
    if state.pending % COMMIT_BATCH == 0:
        state.session.commit()


def _decide_pair(state: _DedupeState, a: PersonRecord, b: PersonRecord) -> None:
    """Score one pair and record the decision (link, queue for review, or skip)."""
    if (a.entity_id, b.entity_id) in state.decided or (b.entity_id, a.entity_id) in state.decided:
        return
    value, method = state.scorer.score(a.profile(), b.profile())
    if classify(value) == "distinct":
        return
    _record_decision(state, a, b, value, method)


def _rows_affected(session: Session, statement: Delete | Update) -> int:
    """Run a bulk DML statement and report how many rows it touched.

    ``synchronize_session=False`` keeps SQLAlchemy from trying to reconcile the
    identity map row by row; :func:`reset_person_links` expires it wholesale
    afterwards instead.
    """
    result = session.execute(statement.execution_options(synchronize_session=False))
    return cast("CursorResult[Any]", result).rowcount


def reset_person_links(session: Session) -> tuple[int, int]:
    """Discard every machine-made person link, keeping human decisions.

    The broken scorer's output is already in the database — the pass ran during
    the 2026-08-25 rebuild and auto-linked hundreds of thousands of pairs on the
    ``initials`` rule that #173 showed to be ~81% wrong. Re-running the pass
    alone would not undo them: ``dedupe_persons`` skips any pair that already
    has a ``PersonMatch``, so the old verdicts would simply be preserved.

    This clears the way for a clean re-run. ``accepted``/``rejected`` rows are
    reviewed by a human and survive, along with the links they justify — the
    same rows ``rebuild_silver`` carries across a rebuild.

    Returns ``(matches deleted, entities unlinked)``.
    """
    # Only an *accepted* match justifies keeping a link; a rejected one is a
    # human saying these are different people.
    accepted = select(PersonMatch.entity_id).where(
        PersonMatch.status == "accepted", PersonMatch.entity_id == Entity.id
    )
    deleted = _rows_affected(
        session,
        delete(PersonMatch).where(PersonMatch.status.not_in(HUMAN_STATUSES)),
    )
    unlinked = _rows_affected(
        session,
        update(Entity)
        .where(Entity.kind == "person", Entity.canonical_entity_id.is_not(None), ~accepted.exists())
        .values(canonical_entity_id=None),
    )
    session.commit()
    # The bulk statements bypassed the identity map; drop any stale Entity or
    # PersonMatch still loaded so a dedupe run in the same session sees the reset.
    session.expire_all()
    log.info("reset %d machine person match(es), unlinked %d entity/ies", deleted, unlinked)
    return deleted, unlinked


def _cluster_canonical(members: frozenset[uuid.UUID], names: dict[uuid.UUID, PersonName]) -> uuid.UUID:
    """The member with the fullest given names, ties broken on the id.

    Chosen once from the whole cluster. Picking a canonical per pair was what
    let one group point at two different entities, since each pair only ever
    saw the two names in front of it.
    """
    return min(members, key=lambda entity_id: (-_given_length(names[entity_id]), str(entity_id)))


def _cluster_edges(session: Session) -> tuple[list[Edge], list[tuple[uuid.UUID, uuid.UUID]]]:
    """The links to cluster, and the pairs a human said not to cluster together.

    ``auto_linked`` is the model's verdict and ``accepted`` a human's; both are
    links. ``rejected`` is a human saying "not the same person", which becomes
    a cannot-link so the pair cannot be reunited by a transitive merge either.
    The corpus carries no human rows at all today; the cannot-links that
    actually bind come from :mod:`constraints`.
    """
    links: list[Edge] = []
    barred: list[tuple[uuid.UUID, uuid.UUID]] = []
    rows = session.execute(
        select(
            PersonMatch.entity_id, PersonMatch.canonical_entity_id, PersonMatch.score, PersonMatch.status
        ).where(PersonMatch.status.in_(("auto_linked", "accepted", "rejected")))
    ).tuples()
    for entity_id, canonical_id, score, status in rows:
        if status == "rejected":
            barred.append((entity_id, canonical_id))
        else:
            links.append(Edge(a=entity_id, b=canonical_id, score=score))
    return links, barred


@dataclass(frozen=True)
class Partition:
    """The person partition and the authority constraints that shaped it."""

    clustering: Clustering
    constraints: Constraints


@dataclass(frozen=True)
class DedupeResult:
    """What one pass decided: the pair counts, and the partition they produced.

    The refusals live on ``partition``, and they are part of the pass's result
    rather than a detail of it: a cannot-link firing on a pair the model scored
    above the auto threshold is a report about the model.
    """

    auto: int
    review: int
    partition: Partition


def apply_clusters(session: Session) -> Partition:
    """Rebuild every person link from the clusters the recorded links imply.

    Every member of a cluster is pointed straight at that cluster's canonical
    and every other person entity is unlinked, so the pointer graph is a
    partition: one hop to a root, no chains, no cycles. Gold relies on this —
    ``_selection._resolve_roots`` reads the column as a dict rather than
    walking it.

    Rebuilding the whole partition rather than patching the pairs that changed
    is what keeps it consistent: one new link can join two existing clusters,
    the canonical of the merged group is not necessarily the canonical of
    either, and a cannot-link can only be checked against a whole group.
    """
    entities = {
        entity.id: entity for entity in session.scalars(select(Entity).where(Entity.kind == "person"))
    }
    names = {entity_id: parse_name(entity.label) for entity_id, entity in entities.items()}
    links, rejected = _cluster_edges(session)
    constraints = authority_constraints(load_records(session, list(entities.values())), links)
    clustering = build_clusters(links, [*rejected, *constraints.cannot_link])

    wanted: dict[uuid.UUID, uuid.UUID | None] = {}
    for members in clustering.clusters:
        canonical = _cluster_canonical(members, names)
        for member in members:
            wanted[member] = None if member == canonical else canonical
    for entity_id, entity in entities.items():
        target = wanted.get(entity_id)
        if entity.canonical_entity_id != target:
            entity.canonical_entity_id = target
    session.commit()
    log.info(
        "%d cluster(s), %d member(s), largest %d; %d merge(s) refused, "
        "%d authority conflict(s) discharged as corroborated",
        len(clustering.clusters),
        clustering.members,
        clustering.largest,
        len(clustering.refused),
        len(constraints.discharged),
    )
    return Partition(clustering=clustering, constraints=constraints)


def dedupe_persons(session: Session) -> DedupeResult:
    """Run the dedupe pass over every person entity."""
    entities = list(session.scalars(select(Entity).where(Entity.kind == "person")))
    records = load_records(session, entities)
    scorer = PersonScorer(default_model(), build_corpus(records))
    log.info("deduping %d person record(s)", len(records))

    state = _DedupeState(
        session=session,
        scorer=scorer,
        decided=set(session.execute(select(PersonMatch.entity_id, PersonMatch.canonical_entity_id)).tuples()),
    )

    for a, b in candidate_pairs(records):
        _decide_pair(state, a, b)

    session.commit()
    partition = apply_clusters(session)
    log.info("auto-linked %d, queued %d for review", state.auto, state.review)
    return DedupeResult(auto=state.auto, review=state.review, partition=partition)
