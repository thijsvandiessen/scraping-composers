"""The post-hoc person dedupe pass.

Blocks the person entities by surname, scores every pair in a block with the
trained Fellegi-Sunter model, and records the decision: pairs above the auto
cut-point are linked (``Entity.canonical_entity_id`` set plus a ``PersonMatch``
row), middling pairs are queued for review, the rest are ignored. Re-running is
idempotent — a pair that already has a ``PersonMatch`` is skipped.

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

from .corpus import PersonRecord, build_corpus, candidate_pairs, load_records
from .match import PersonScorer, classify, default_model

log = logging.getLogger(__name__)

COMMIT_BATCH = 1000

# Decisions a human made, which a re-run must never discard.
HUMAN_STATUSES = ("accepted", "rejected")


def _given_length(record: PersonRecord) -> int:
    """Total spelled-out length of the given names — so "Johann Sebastian"
    counts as fuller than the initials "J. S." (same token count)."""
    return sum(len(token) for token in record.name.given)


def _canonical_and_duplicate(a: PersonRecord, b: PersonRecord) -> tuple[PersonRecord, PersonRecord]:
    """The fuller name (most spelled-out given names; id tie-break) is canonical."""
    la, lb = _given_length(a), _given_length(b)
    if la != lb:
        return (a, b) if la > lb else (b, a)
    return (a, b) if str(a.entity_id) < str(b.entity_id) else (b, a)


@dataclass
class _DedupeState:
    """Scorer, preloaded lookups and progress counters for one dedupe pass."""

    session: Session
    scorer: PersonScorer
    entities: dict[uuid.UUID, Entity]
    decided: set[tuple[uuid.UUID, uuid.UUID]]
    linked: set[uuid.UUID]
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
        if duplicate.entity_id not in state.linked:
            state.entities[duplicate.entity_id].canonical_entity_id = canonical.entity_id
            state.linked.add(duplicate.entity_id)
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


def dedupe_persons(session: Session) -> tuple[int, int]:
    """Run the dedupe pass. Returns (auto-linked count, needs-review count)."""
    entities = list(session.scalars(select(Entity).where(Entity.kind == "person")))
    records = load_records(session, entities)
    scorer = PersonScorer(default_model(), build_corpus(records))
    log.info("deduping %d person record(s)", len(records))

    state = _DedupeState(
        session=session,
        scorer=scorer,
        entities={entity.id: entity for entity in entities},
        decided=set(session.execute(select(PersonMatch.entity_id, PersonMatch.canonical_entity_id)).tuples()),
        linked={entity.id for entity in entities if entity.canonical_entity_id is not None},
    )

    for a, b in candidate_pairs(records):
        _decide_pair(state, a, b)

    session.commit()
    log.info("auto-linked %d, queued %d for review", state.auto, state.review)
    return state.auto, state.review
