"""Tests for the automatically labelled evaluation set and its metrics.

The last test is the one that matters: it pins the shipped model's measured
advantage over the pre-#173 scorer on the committed dataset, so a change that
quietly makes matching worse fails CI instead of being argued about.
"""

import uuid
from pathlib import Path

import pytest
from composer_warehouse.persons.corpus import PersonRecord
from composer_warehouse.persons.evaluation import (
    MATCH,
    NON_MATCH,
    LabelledPair,
    Metrics,
    downsample,
    evaluate,
    label_pair,
    legacy_score,
    model_scorer,
    read_dataset,
    split,
    write_dataset,
)
from composer_warehouse.persons.extract import parse_name
from composer_warehouse.persons.match import AUTO_THRESHOLD, PersonScorer, default_model

DATASET = Path(__file__).parent / "data" / "person_eval_pairs.jsonl.gz"


def _record(label: str, **kwargs: object) -> PersonRecord:
    return PersonRecord(entity_id=uuid.uuid4(), label=label, name=parse_name(label), **kwargs)  # type: ignore[arg-type]


def test_corroborated_dates_label_a_match() -> None:
    a = _record(
        "Bach, J.S.",
        birth_year=1685,
        death_year=1750,
        birth_sources=frozenset({1, 2}),
        death_sources=frozenset({1, 2}),
    )
    b = _record(
        "Johann Sebastian Bach",
        birth_year=1685,
        death_year=1750,
        birth_sources=frozenset({3}),
        death_sources=frozenset({3}),
    )
    assert label_pair(a, b) == (MATCH, "dates_corroborated")


def test_a_single_source_is_not_corroboration() -> None:
    a = _record(
        "Bach, J.S.",
        birth_year=1685,
        death_year=1750,
        birth_sources=frozenset({1}),
        death_sources=frozenset({1}),
    )
    b = _record(
        "Bach, Johann Sebastian",
        birth_year=1685,
        death_year=1750,
        birth_sources=frozenset({1}),
        death_sources=frozenset({1}),
    )
    assert label_pair(a, b) is None


def test_a_generation_apart_labels_a_non_match() -> None:
    # The father, not the son.
    a = _record("Bach, Johann Sebastian", birth_years=frozenset({1685}))
    b = _record("Bach, Johann Christian", birth_years=frozenset({1735}))
    assert label_pair(a, b) == (NON_MATCH, "year_conflict")


def test_distinct_musicbrainz_ids_label_a_non_match() -> None:
    a = _record("Smith, John", musicbrainz_ids=frozenset({"aaa"}))
    b = _record("Smith, John", musicbrainz_ids=frozenset({"bbb"}))
    assert label_pair(a, b) == (NON_MATCH, "distinct_musicbrainz")


def test_a_contested_pair_is_left_unlabelled() -> None:
    # Dates say the same person, MusicBrainz says two. Refuse to guess.
    shared = {
        "birth_year": 1900,
        "death_year": 1970,
        "birth_sources": frozenset({1, 2}),
        "death_sources": frozenset({1, 2}),
    }
    a = _record("Muller, Hans", musicbrainz_ids=frozenset({"aaa"}), **shared)
    b = _record("Muller, Hans", musicbrainz_ids=frozenset({"bbb"}), **shared)
    assert label_pair(a, b) is None


def test_a_curated_alias_labels_a_match() -> None:
    a = _record("Schonberg, Arnold", aliases=(parse_name("Arnold Schoenberg"),))
    b = _record("Schonberg, Arnold Franz Walter")
    assert label_pair(a, b) is None  # the alias must name the *other* record
    c = _record("Arnold Schoenberg")
    assert label_pair(a, c) == (MATCH, "alias_identity")


def test_a_reordered_name_is_not_treated_as_a_different_one() -> None:
    # Same name, comma-inverted. Every scorer gets this right, so admitting it
    # as ground truth would only flatter them.
    a = _record("Beethoven, Ludwig van", aliases=(parse_name("Ludwig van Beethoven"),))
    b = _record("Ludwig van Beethoven")
    assert label_pair(a, b) is None


def _pair(a: str, b: str, label: str = NON_MATCH, provenance: str = "year_conflict") -> LabelledPair:
    return LabelledPair(a, b, None, None, None, None, label, provenance, 0.0)


def test_split_is_deterministic_and_order_independent() -> None:
    pairs = [_pair(f"Smith, A{i}", f"Smith, B{i}") for i in range(400)]
    train, test = split(pairs)
    again_train, _ = split(list(reversed(pairs)))
    assert {(p.a_label, p.b_label) for p in train} == {(p.a_label, p.b_label) for p in again_train}
    assert train and test and len(train) + len(test) == 400


def test_downsample_records_the_weight_that_undoes_it() -> None:
    pairs = [_pair(f"Smith, A{i}", f"Smith, B{i}") for i in range(1000)]
    kept = downsample(pairs, {"year_conflict": 100})
    assert len(kept) == 100
    assert sum(p.weight for p in kept) == pytest.approx(1000.0)


def test_downsample_keeps_protected_rows_whole() -> None:
    pairs = [_pair(f"Smith, A{i}", f"Smith, B{i}") for i in range(1000)]
    protected = {p.a_label for p in pairs[:7]}
    kept = downsample(pairs, {"year_conflict": 100}, protect=lambda p: p.a_label in protected)
    assert len(kept) == 107
    assert [p.weight for p in kept if p.a_label in protected] == [1.0] * 7
    assert sum(p.weight for p in kept) == pytest.approx(1000.0)


def test_metrics_use_the_weights() -> None:
    metrics = Metrics().add(NON_MATCH, predicted_match=True, weight=20.0)
    assert metrics.false_positive == 20.0
    assert metrics.precision == 0.0


def test_dataset_round_trips(tmp_path: Path) -> None:
    pairs = [_pair("Smith, Anna", "Smith, Boris"), _pair("Lee, Ed. S.", "Lee, Ella")]
    path = tmp_path / "eval.jsonl.gz"
    assert write_dataset(path, pairs) == 2
    assert {(p.a_label, p.b_label) for p in read_dataset(path)} == {(p.a_label, p.b_label) for p in pairs}


def test_the_committed_dataset_is_present_and_labelled_from_four_rules() -> None:
    pairs = read_dataset(DATASET)
    assert len(pairs) > 10_000
    assert {p.provenance for p in pairs} == {
        "dates_corroborated",
        "alias_identity",
        "distinct_musicbrainz",
        "year_conflict",
    }
    assert {p.label for p in pairs} == {MATCH, NON_MATCH}


def test_the_model_beats_the_pre_173_scorer_on_precision_and_recall() -> None:
    """The acceptance criterion of #173, as a test.

    Measured on the held-out split only. The baseline is the old scorer, bug
    intact, at the threshold it actually shipped with.
    """
    _, test = split(read_dataset(DATASET))
    scorer = PersonScorer(default_model())

    model = evaluate(test, model_scorer(scorer), AUTO_THRESHOLD)["overall"]
    baseline = evaluate(test, legacy_score, 0.90)["overall"]

    assert model.precision > baseline.precision
    assert model.recall >= baseline.recall
    assert model.precision >= 0.95
    # The old scorer's false positives were the whole complaint; most must go.
    assert model.false_positive < baseline.false_positive / 5


def test_the_name_comparison_alone_is_far_better_than_the_old_one() -> None:
    """With the dates hidden, the labels are independent of everything the
    scorer sees — the cleanest measure of the given-name defect itself."""
    _, test = split(read_dataset(DATASET))
    scorer = PersonScorer(default_model())

    model = evaluate(test, model_scorer(scorer, with_years=False), AUTO_THRESHOLD)["overall"]
    baseline = evaluate(test, lambda p: legacy_score(p, with_years=False), 0.90)["overall"]

    assert baseline.precision < 0.25  # the defect: most auto-links were wrong
    assert model.precision > 0.85
