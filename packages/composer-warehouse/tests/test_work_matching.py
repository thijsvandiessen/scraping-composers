"""Tests for scoring work candidates and deciding match/review/create."""

import random
import uuid
from dataclasses import replace
from difflib import SequenceMatcher

import pytest
from composer_warehouse.works import match
from composer_warehouse.works.extract import WorkFeatures, extract_features
from composer_warehouse.works.match import (
    AUTO_THRESHOLD,
    REVIEW_THRESHOLD,
    Candidate,
    best_match,
    resolve,
    score,
)


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


BACH = "Bach, Johann Sebastian"


def test_a_range_and_a_piece_inside_it_conflict() -> None:
    collection = extract_features("6 Brandenburg Concertos, BWV 1046-1051", composer=BACH)
    piece = extract_features("Brandenburg Concerto No.1, BWV 1046", composer=BACH)
    assert score(collection, piece) == (0.05, "catalogue_conflict")
    result = resolve(piece, [Candidate(uuid.uuid4(), collection)])
    assert result.status == "created"


def test_a_list_and_its_first_piece_conflict() -> None:
    pair = extract_features("Brandenburg Concertos, BWV 1046, 1047", composer=BACH)
    piece = extract_features("Brandenburg Concerto No.1, BWV 1046", composer=BACH)
    assert score(pair, piece) == (0.05, "catalogue_conflict")


def test_the_same_range_however_spelled_matches() -> None:
    a = extract_features("15 Inventions, BWV 772-786", composer=BACH)
    b = extract_features("Fifteen Two-Part Inventions BWV 772\u2013786", composer=BACH)
    assert score(a, b) == (0.97, "catalogue")


def test_a_structured_range_conflicts_with_the_first_piece() -> None:
    collection = extract_features("12 Concerti Grossi, Op.6", structured=["Op.6 ; HWV 319-330"])
    piece = extract_features("Concerto grosso in G major HWV 319")
    assert score(collection, piece)[1] == "catalogue_conflict"


def _score_every_candidate(
    features: WorkFeatures, candidates: list[Candidate]
) -> tuple[float, str, uuid.UUID | None]:
    """What ``best_match`` must return: ``score`` against each candidate in
    turn, the first of equal best scores winning."""
    best: tuple[float, str, uuid.UUID | None] = (0.0, "new", None)
    for cand in candidates:
        value, method = score(features, cand.features)
        if value > best[0]:
            best = (value, method, cand.work_id)
    return best


_WORDS = (
    "Walzer",
    "Polka",
    "Symphony",
    "Sonata",
    "Ouverture",
    "Rosen",
    "aus",
    "dem",
    "Süden",
    "Wiener",
    "Blut",
)
_DECORATIONS = (
    "",
    " No. 2",
    " No. 5",
    " in A major",
    " in c-moll",
    ", Op. 92",
    ", Op. 314",
    ", K. 550",
    ", BWV 1048",
    ", BWV 1046",
    ", BWV 1046-1051",
    ", BWV 1046, 1047",
)


def _title(rng: random.Random) -> str:
    words = " ".join(rng.choice(_WORDS) for _ in range(rng.randint(1, 4)))
    title = words + rng.choice(_DECORATIONS)
    return f"Allegro (from {title})" if rng.random() < 0.1 else title


def test_best_match_is_scoring_every_candidate() -> None:
    # The pruned search (#366) must give exactly what the plain scan gives, ties
    # included: repeated titles score equal, and the first of them must win.
    rng = random.Random(366)
    candidates = [
        Candidate(uuid.uuid4(), extract_features(_title(rng), composer=rng.choice((MOZART, BACH))))
        for _ in range(400)
    ]
    candidates.append(Candidate(uuid.uuid4(), extract_features("", composer=MOZART)))
    for _ in range(300):
        features = extract_features(_title(rng), composer=rng.choice((MOZART, BACH)))
        if rng.random() < 0.1:
            features = replace(
                features, identifiers=features.identifiers | {("wikidata", f"Q{rng.randint(1, 3)}")}
            )
        assert best_match(features, candidates) == _score_every_candidate(features, candidates)


def test_best_match_compares_few_titles_against_a_large_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    # A prolific composer has thousands of works; comparing a mention with each
    # of them made re-ingesting the Vienna Philharmonic take 44 minutes (#366).
    compared: list[str] = []

    def counting(junk: None, a: str, b: str) -> SequenceMatcher[str]:
        compared.append(b)
        return SequenceMatcher(junk, a, b)

    monkeypatch.setattr(match, "SequenceMatcher", counting)
    rng = random.Random(44)
    titles = {" ".join(rng.choice(_WORDS) for _ in range(rng.randint(1, 6))) for _ in range(5000)}
    candidates = [Candidate(uuid.uuid4(), extract_features(title)) for title in sorted(titles)]
    target = candidates[len(candidates) // 2]

    assert best_match(target.features, candidates) == (1.0, "title_fuzzy", target.work_id)
    assert len(compared) < len(candidates) // 20
