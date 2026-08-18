"""Work resolution: turn a raw (composer, title) mention into a canonical work.

``extract`` pulls structured features (catalogue/opus numbers, key, type,
number) out of a raw title; ``match`` scores those features against existing
works by the same composer and decides whether to auto-match, flag for review,
or create a new work. The ingest pipeline drives this for every
``RawWorkMention``.
"""

from __future__ import annotations

import uuid

from composer_models import WorkTitle
from sqlalchemy import select
from sqlalchemy.orm import Session

from .extract import WorkFeatures, extract_features, normalize_title
from .match import (
    AUTO_THRESHOLD,
    REVIEW_THRESHOLD,
    Candidate,
    MatchResult,
    best_match,
    classify,
    resolve,
    score,
)


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


__all__ = [
    "AUTO_THRESHOLD",
    "REVIEW_THRESHOLD",
    "Candidate",
    "MatchResult",
    "WorkFeatures",
    "add_alias",
    "best_match",
    "classify",
    "extract_features",
    "normalize_title",
    "resolve",
    "score",
]
