"""Score a work mention against candidate works and decide the outcome.

Hard identifiers dominate: a matching catalogue number (BWV/K/D/...) or opus
number is near-certain identity, and a *conflicting* one is near-certain
non-identity. Absent those, the work type, number and key sharpen a fuzzy
``difflib`` comparison of the residual title. Thresholds split the score into
auto-match / needs-review / create-new; they are deliberately simple constants
so they can be tuned and re-applied via ``rematch``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher

from .extract import WorkFeatures

AUTO_THRESHOLD = 0.90
REVIEW_THRESHOLD = 0.60


@dataclass(frozen=True)
class Candidate:
    work_id: uuid.UUID
    features: WorkFeatures


@dataclass(frozen=True)
class MatchResult:
    status: str  # auto_matched | needs_review | created
    score: float
    method: str
    work_id: uuid.UUID | None  # the matched work (None when creating new / under review)
    candidate_work_id: uuid.UUID | None  # best near-miss, for the reviewer


def score(a: WorkFeatures, b: WorkFeatures) -> tuple[float, str]:  # noqa: C901
    """Similarity of two works in [0, 1] with the method that decided it."""
    # Catalogue numbers are authoritative when both sides have one.
    if a.catalogue_prefix and b.catalogue_prefix:
        if (a.catalogue_prefix, a.catalogue_number) == (b.catalogue_prefix, b.catalogue_number):
            return 0.97, "catalogue"
        return 0.05, "catalogue_conflict"

    # Opus numbers are next strongest.
    if a.opus_number and b.opus_number:
        if a.opus_number != b.opus_number:
            return 0.10, "opus_conflict"
        if a.number is not None and b.number is not None and a.number != b.number:
            return 0.15, "opus_number_conflict"  # same opus, different work within it
        return 0.92, "opus"

    ratio = SequenceMatcher(None, a.core_title, b.core_title).ratio()
    # A differing explicit number/key is strong evidence of a different work.
    if a.number is not None and b.number is not None and a.number != b.number:
        ratio *= 0.5
    if a.musical_key and b.musical_key and a.musical_key != b.musical_key:
        ratio *= 0.7
    bonus = 0.0
    if a.work_type and a.work_type == b.work_type:
        bonus += 0.05
    if a.number is not None and a.number == b.number:
        bonus += 0.05
    if a.musical_key and a.musical_key == b.musical_key:
        bonus += 0.05
    return min(1.0, ratio + bonus), "title_fuzzy"


def best_match(features: WorkFeatures, candidates: list[Candidate]) -> tuple[float, str, uuid.UUID | None]:
    """The highest-scoring candidate: (score, method, work_id)."""
    best: tuple[float, str, uuid.UUID | None] = (0.0, "new", None)
    for cand in candidates:
        s, method = score(features, cand.features)
        if s > best[0]:
            best = (s, method, cand.work_id)
    return best


def classify(score_value: float) -> str:
    if score_value >= AUTO_THRESHOLD:
        return "auto_matched"
    if score_value >= REVIEW_THRESHOLD:
        return "needs_review"
    return "created"


def resolve(features: WorkFeatures, candidates: list[Candidate]) -> MatchResult:
    """Decide what to do with a mention given its candidate works."""
    best_score, method, work_id = best_match(features, candidates)
    status = classify(best_score)
    if status == "auto_matched":
        return MatchResult(status, best_score, method, work_id, work_id)
    if status == "needs_review":
        return MatchResult(status, best_score, method, None, work_id)
    return MatchResult("created", best_score, "new", None, None)
