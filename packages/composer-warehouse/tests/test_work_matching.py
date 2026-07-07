"""Tests for scoring work candidates and deciding match/review/create."""

import uuid

from composer_warehouse.works.extract import extract_features
from composer_warehouse.works.match import AUTO_THRESHOLD, REVIEW_THRESHOLD, Candidate, resolve, score


def _candidate(title: str) -> Candidate:
    return Candidate(uuid.uuid4(), extract_features(title))


def test_matching_catalogue_numbers_score_near_certain() -> None:
    a = extract_features("Goldberg Variations, BWV 988")
    b = extract_features("Aria with Diverse Variations, BWV 988")
    s, method = score(a, b)
    assert method == "catalogue"
    assert s >= AUTO_THRESHOLD


def test_conflicting_catalogue_numbers_score_near_zero() -> None:
    a = extract_features("Prelude in C major, BWV 846")
    b = extract_features("Prelude in C minor, BWV 847")
    s, method = score(a, b)
    assert method == "catalogue_conflict"
    assert s < REVIEW_THRESHOLD


def test_same_opus_different_number_is_a_different_work() -> None:
    a = extract_features("String Quartet, Op. 18 No. 1")
    b = extract_features("String Quartet, Op. 18 No. 2")
    s, _ = score(a, b)
    assert s < REVIEW_THRESHOLD


def test_exact_catalogue_auto_matches() -> None:
    cand = _candidate("Symphony No. 5 in C minor, Op. 67")
    feats = extract_features("Sinfonie Nr. 5 c-moll op. 67")
    result = resolve(feats, [cand])
    assert result.status == "auto_matched"
    assert result.work_id == cand.work_id


def test_no_candidates_creates_new_work() -> None:
    result = resolve(extract_features("Symphony No. 5 in C minor, Op. 67"), [])
    assert result.status == "created"
    assert result.work_id is None
    assert result.method == "new"


def test_similar_title_without_hard_identifier_needs_review() -> None:
    # Same composer, similar wording, but no opus/catalogue to confirm identity.
    cand = _candidate("Songs of a Wayfarer")
    feats = extract_features("Songs of a Traveller")
    result = resolve(feats, [cand])
    assert result.status == "needs_review"
    assert result.work_id is None
    assert result.candidate_work_id == cand.work_id
    assert REVIEW_THRESHOLD <= result.score < AUTO_THRESHOLD
