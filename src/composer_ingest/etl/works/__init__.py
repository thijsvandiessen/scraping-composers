"""Work resolution: turn a raw (composer, title) mention into a canonical work.

``extract`` pulls structured features (catalogue/opus numbers, key, type,
number) out of a raw title; ``match`` scores those features against existing
works by the same composer and decides whether to auto-match, flag for review,
or create a new work. The ingest pipeline drives this for every
``RawWorkMention``.
"""

from __future__ import annotations

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

__all__ = [
    "AUTO_THRESHOLD",
    "REVIEW_THRESHOLD",
    "Candidate",
    "MatchResult",
    "WorkFeatures",
    "best_match",
    "classify",
    "extract_features",
    "normalize_title",
    "resolve",
    "score",
]
