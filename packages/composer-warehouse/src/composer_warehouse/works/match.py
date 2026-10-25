"""Score a work mention against candidate works and decide the outcome.

Hard identifiers dominate: a shared external id (a Wikidata item, a
MusicBrainz work, an ISWC), a shared catalogue number (BWV/KV/D/...) or an
opus number is near-certain identity, and a *conflicting* one is near-certain
non-identity. A range or list (``BWV 1046-1051``) is a value of its own, so a
set and a piece inside it conflict like two different numbers. A part,
arrangement or version never matches the work it derives from, though they
share its number; between two derived works the title decides. Absent those,
the work type, number and key sharpen a fuzzy ``difflib`` comparison of the
residual title. Thresholds split the score into
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


#: Identifier schemes that name one work across sources, with the score a
#: shared one gives. Anything else in ``WorkFeatures.identifiers`` is a
#: catalogue number.
EXTERNAL_SCHEMES: dict[str, float] = {"wikidata": 0.99, "musicbrainz": 0.99, "iswc": 0.99}
CATALOGUE_SCORE = 0.97


@dataclass(frozen=True)
class Candidate:
    """An existing work to match against. ``features`` carries every
    identifier the work has accumulated, not just its title's."""

    work_id: uuid.UUID
    features: WorkFeatures


@dataclass(frozen=True)
class MatchResult:
    status: str  # auto_matched | needs_review | created
    score: float
    method: str
    work_id: uuid.UUID | None  # the matched work (None when creating new / under review)
    candidate_work_id: uuid.UUID | None  # best near-miss, for the reviewer


def _schemes(ids: frozenset[tuple[str, str]]) -> dict[str, set[str]]:
    by_scheme: dict[str, set[str]] = {}
    for scheme, value in ids:
        by_scheme.setdefault(scheme, set()).add(value)
    return by_scheme


def _shared_scheme_conflict(a: frozenset[tuple[str, str]], b: frozenset[tuple[str, str]]) -> str | None:
    """A scheme both sides number, with no value in common."""
    ours, theirs = _schemes(a), _schemes(b)
    return next((s for s in sorted(ours.keys() & theirs.keys()) if not ours[s] & theirs[s]), None)


def _derivation_score(a: WorkFeatures, b: WorkFeatures) -> tuple[float, str] | None:
    """A part/arrangement/version shares its parent's number but is not it.
    Between two derived works, different parents settle it; otherwise their
    own titles ("Allegro" vs "Adagio") decide."""
    if a.derivation != b.derivation:
        return 0.05, "derivation_conflict"
    if _shared_scheme_conflict(a.parent_identifiers, b.parent_identifiers) is not None:
        return 0.05, "parent_conflict"
    return None


def _strong_id_score(a: WorkFeatures, b: WorkFeatures) -> tuple[float, str] | None:
    """A shared external id or catalogue number, or one scheme numbered
    differently on each side."""
    shared = {scheme for scheme, _ in a.identifiers & b.identifiers}
    for scheme, value in EXTERNAL_SCHEMES.items():
        if scheme in shared:
            return value, scheme
    if shared:
        return CATALOGUE_SCORE, "catalogue"
    if (conflict := _shared_scheme_conflict(a.identifiers, b.identifiers)) is not None:
        return 0.05, "external_conflict" if conflict in EXTERNAL_SCHEMES else "catalogue_conflict"
    return None


def _opus_score(a: WorkFeatures, b: WorkFeatures) -> tuple[float, str] | None:
    if not (a.opus_number and b.opus_number):
        return None
    if a.opus_number != b.opus_number:
        return 0.10, "opus_conflict"
    if a.number is not None and b.number is not None and a.number != b.number:
        return 0.15, "opus_number_conflict"  # same opus, different work within it
    return 0.92, "opus"


def _identifier_score(a: WorkFeatures, b: WorkFeatures) -> tuple[float, str] | None:
    """The score decided by hard identifiers, or None when they don't settle it."""
    if a.derivation is not None or b.derivation is not None:
        return _derivation_score(a, b)
    return _strong_id_score(a, b) or _opus_score(a, b)


def score(a: WorkFeatures, b: WorkFeatures) -> tuple[float, str]:
    """Similarity of two works in [0, 1] with the method that decided it."""
    if (identified := _identifier_score(a, b)) is not None:
        return identified

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
