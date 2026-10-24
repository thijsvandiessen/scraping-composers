"""Tests for scoring work candidates and deciding match/review/create."""

import uuid
from dataclasses import replace

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


MOZART = "Mozart, Wolfgang Amadeus"


def test_k_and_kv_match_as_one_koechel_number() -> None:
    a = extract_features("Symphony no. 40 in G minor K. 550", composer=MOZART)
    b = extract_features("Symfonie nr. 40 in g kl.t., KV 550", composer=MOZART)
    assert score(a, b) == (0.97, "catalogue")


def test_different_koechel_numbers_conflict() -> None:
    a = extract_features("Symphony No. 40, K. 550", composer=MOZART)
    b = extract_features("Symphony No. 41, KV 551", composer=MOZART)
    assert score(a, b)[1] == "catalogue_conflict"


def test_different_catalogues_neither_match_nor_conflict() -> None:
    # Old and new Köchel numbers are different schemes: no shared key, no conflict
    a = extract_features("Symphony No. 25, K. 183", composer=MOZART)
    b = extract_features("Symphony No. 25, K. 173dB", composer=MOZART)
    assert score(a, b)[1] == "title_fuzzy"


def test_a_shared_wikidata_item_is_near_certain() -> None:
    a = replace(extract_features("Eine kleine Nachtmusik"), identifiers=frozenset({("wikidata", "Q201234")}))
    b = replace(extract_features("Serenade No. 13"), identifiers=frozenset({("wikidata", "Q201234")}))
    assert score(a, b) == (0.99, "wikidata")


def test_different_wikidata_items_conflict() -> None:
    a = replace(extract_features("Serenade"), identifiers=frozenset({("wikidata", "Q1")}))
    b = replace(extract_features("Serenade"), identifiers=frozenset({("wikidata", "Q2")}))
    assert score(a, b) == (0.05, "external_conflict")


def test_a_part_never_matches_its_parent() -> None:
    parent = extract_features("Brandenburg Concerto No. 3 in G major, BWV 1048")
    part = extract_features("Allegro (From Brandenburg Concerto No. 3, BWV 1048)")
    arrangement = extract_features("Brandenburg Concerto No. 3, BWV 1048 (arr. for woodwind quintet)")
    assert score(parent, part) == (0.05, "derivation_conflict")
    assert score(parent, arrangement) == (0.05, "derivation_conflict")
    assert score(part, arrangement) == (0.05, "derivation_conflict")


def test_parts_of_different_parents_conflict() -> None:
    a = extract_features("Allegro (from Brandenburg Concerto No. 3, BWV 1048)")
    b = extract_features("Allegro (from Brandenburg Concerto No. 4, BWV 1049)")
    assert score(a, b) == (0.05, "parent_conflict")


def test_two_parts_of_one_parent_are_told_apart_by_title() -> None:
    a = extract_features("Allegro (from Brandenburg Concerto No. 3, BWV 1048)")
    b = extract_features("Adagio (from Brandenburg Concerto No. 3, BWV 1048)")
    s, method = score(a, b)
    assert method == "title_fuzzy"
    assert s < AUTO_THRESHOLD
