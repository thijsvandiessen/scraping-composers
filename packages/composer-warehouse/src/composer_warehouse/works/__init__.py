"""Work resolution: turn a raw (composer, title) mention into a canonical work.

``extract`` pulls structured features (catalogue/opus numbers, key, type,
number) out of a raw title; ``match`` scores those features against existing
works by the same composer and decides whether to auto-match, flag for review,
or create a new work. The ingest pipeline drives this for every
``RawWorkMention``. ``sources`` reads the catalogue numbers and external ids
a source states outside the title; ``links`` points parts, arrangements and
versions at the work they derive from.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import replace

from composer_models import Entity, Work, WorkIdentifier, WorkTitle
from composer_models.normalize import MAX_KEY_CHARS
from sqlalchemy import select
from sqlalchemy.orm import Session

from .extract import DERIVATIONS, WorkFeatures, extract_features, normalize_title
from .match import (
    AUTO_THRESHOLD,
    EXTERNAL_SCHEMES,
    REVIEW_THRESHOLD,
    Candidate,
    MatchResult,
    best_match,
    classify,
    resolve,
    score,
)
from .sources import external_ids, has_catalogue, mention_features, part_of, structured_catalogue


def add_alias(session: Session, work_id: uuid.UUID, title: str, source_id: int) -> None:
    """Record ``title`` as an alias of a work, once per (work, title, source)."""
    title_key = normalize_title(title)
    exists = session.scalar(
        select(WorkTitle.id).where(
            WorkTitle.work_id == work_id,
            WorkTitle.title_key == title_key,
            WorkTitle.source_id == source_id,
        )
    )
    if exists is None:
        session.add(WorkTitle(work_id=work_id, title=title, title_key=title_key, source_id=source_id))


def add_identifiers(
    session: Session, work_id: uuid.UUID, identifiers: Iterable[tuple[str, str]], source_id: int
) -> None:
    """Record a work's identifiers as one source stated them, once each."""
    for scheme, value in identifiers:
        value = value[:MAX_KEY_CHARS]
        exists = session.scalar(
            select(WorkIdentifier.id).where(
                WorkIdentifier.work_id == work_id,
                WorkIdentifier.scheme == scheme,
                WorkIdentifier.value == value,
                WorkIdentifier.source_id == source_id,
            )
        )
        if exists is None:
            session.add(WorkIdentifier(work_id=work_id, scheme=scheme, value=value, source_id=source_id))


def with_identifiers(features: WorkFeatures, identifiers: Iterable[tuple[str, str]]) -> WorkFeatures:
    """``features`` also carrying ``identifiers`` (a work's accumulated ones)."""
    merged = features.identifiers | frozenset(identifiers)
    return features if merged == features.identifiers else replace(features, identifiers=merged)


def refresh_candidate(
    candidates: list[Candidate], work_id: uuid.UUID, identifiers: Iterable[tuple[str, str]]
) -> None:
    """Let ``work_id``'s candidate also carry ``identifiers``, in place."""
    for i, candidate in enumerate(candidates):
        if candidate.work_id == work_id:
            merged = with_identifiers(candidate.features, identifiers)
            if merged is not candidate.features:
                candidates[i] = Candidate(work_id, merged)
            return


def load_candidates(session: Session) -> dict[uuid.UUID | None, list[Candidate]]:
    """Every work as a match candidate, keyed by composer.

    The features are re-read from the canonical title *for its composer* (so a
    bare ``K. 550`` is Köchel again) and carry every identifier stored for the
    work -- the structured catalogue numbers and external ids its sources
    stated, which the title alone would lose on the next run.
    """
    stored: dict[uuid.UUID, set[tuple[str, str]]] = {}
    for work_id, scheme, value in session.execute(
        select(WorkIdentifier.work_id, WorkIdentifier.scheme, WorkIdentifier.value)
    ).tuples():
        stored.setdefault(work_id, set()).add((scheme, value))

    candidates: dict[uuid.UUID | None, list[Candidate]] = {}
    for work_id, composer_id, title, composer in session.execute(
        select(Work.id, Work.composer_entity_id, Work.canonical_title, Entity.label).outerjoin(
            Entity, Entity.id == Work.composer_entity_id
        )
    ).tuples():
        features = with_identifiers(extract_features(title, composer=composer), stored.get(work_id, ()))
        candidates.setdefault(composer_id, []).append(Candidate(work_id, features))
    return candidates


__all__ = [
    "AUTO_THRESHOLD",
    "DERIVATIONS",
    "EXTERNAL_SCHEMES",
    "REVIEW_THRESHOLD",
    "Candidate",
    "MatchResult",
    "WorkFeatures",
    "add_alias",
    "add_identifiers",
    "best_match",
    "classify",
    "external_ids",
    "extract_features",
    "has_catalogue",
    "load_candidates",
    "mention_features",
    "normalize_title",
    "part_of",
    "refresh_candidate",
    "resolve",
    "score",
    "structured_catalogue",
    "with_identifiers",
]
