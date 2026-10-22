"""Tests for fitting the person linkage model from a warehouse."""

import functools
import math
import uuid

import pytest
from composer_schema import SourceClaim
from composer_warehouse.persons import training
from composer_warehouse.persons.corpus import PersonRecord, build_corpus, candidate_pairs
from composer_warehouse.persons.evaluation import MATCH, NON_MATCH, LabelledPair, build_labels
from composer_warehouse.persons.extract import parse_name
from composer_warehouse.persons.match import person_comparisons
from composer_warehouse.persons.training import (
    NAME_ONLY,
    collect_patterns,
    estimate_prior,
    sample_non_match_levels,
    to_vectors,
    train,
)
from composer_warehouse.testing import FakeSource, ingest_source, person
from sqlalchemy.orm import Session

# The shipped default samples three million random pairs; a test corpus of a
# few dozen people is described just as well by far fewer.
SMALL_SAMPLE = 20_000


@pytest.fixture
def small_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        training,
        "sample_non_match_levels",
        functools.partial(sample_non_match_levels, pairs=SMALL_SAMPLE),
    )


def _record(label: str, **kwargs: object) -> PersonRecord:
    return PersonRecord(entity_id=uuid.uuid4(), label=label, name=parse_name(label), **kwargs)  # type: ignore[arg-type]


def _pair(provenance: str, label: str = MATCH, weight: float = 1.0) -> LabelledPair:
    return LabelledPair(
        a_label="Bach, J.S.",
        b_label="Bach, Johann Sebastian",
        a_birth=1685,
        b_birth=1685,
        a_death=1750,
        b_death=1750,
        label=label,
        provenance=provenance,
        tf_bits=0.0,
        weight=weight,
    )


def _records() -> list[PersonRecord]:
    """One pair for each labelling rule, one undecidable pair, and filler."""
    return [
        _record(
            "Bach, J.S.",
            birth_year=1685,
            death_year=1750,
            birth_sources=frozenset({1, 2}),
            death_sources=frozenset({1, 2}),
        ),
        _record(
            "Bach, Johann Sebastian",
            birth_year=1685,
            death_year=1750,
            birth_years=frozenset({1685}),
            death_years=frozenset({1750}),
        ),
        _record("Bach, Johann Christian", birth_years=frozenset({1735})),
        _record("Mozart, Leopold", musicbrainz_ids=frozenset({"mb-1"})),
        _record("Mozart, Wolfgang", musicbrainz_ids=frozenset({"mb-2"})),
        _record("Beethoven, Ludwig van", aliases=(parse_name("Louis van Beethoven"),)),
        _record("Louis van Beethoven"),
        _record("Haydn, Joseph"),
        _record("Haydn, Michael"),
        *(_record(f"Smith, Filler{i}") for i in range(10)),
    ]


# ---------------------------------------------------------------------------
# build_labels
# ---------------------------------------------------------------------------


def test_build_labels_keeps_only_pairs_a_rule_decides() -> None:
    labelled = build_labels(_records(), lambda a, b: 1.23456)

    by_names = {frozenset((p.a_label, p.b_label)): (p.label, p.provenance) for p in labelled}
    assert by_names[frozenset(("Bach, J.S.", "Bach, Johann Sebastian"))] == (MATCH, "dates_corroborated")
    assert by_names[frozenset(("Bach, Johann Sebastian", "Bach, Johann Christian"))] == (
        NON_MATCH,
        "year_conflict",
    )
    assert by_names[frozenset(("Mozart, Leopold", "Mozart, Wolfgang"))] == (NON_MATCH, "distinct_musicbrainz")
    assert by_names[frozenset(("Beethoven, Ludwig van", "Louis van Beethoven"))] == (MATCH, "alias_identity")
    # Same surname, no evidence either way: not labelled, not guessed at.
    assert frozenset(("Haydn, Joseph", "Haydn, Michael")) not in by_names
    assert not any(p.a_label.startswith("Smith") for p in labelled)


def test_build_labels_records_rounded_tf_bits_and_both_sides_years() -> None:
    labelled = build_labels(_records(), lambda a, b: 1.23456)

    bach = next(p for p in labelled if p.provenance == "dates_corroborated")
    assert bach.tf_bits == 1.2346
    assert (bach.a_birth, bach.b_birth, bach.a_death, bach.b_death) == (1685, 1685, 1750, 1750)
    assert bach.weight == 1.0


# ---------------------------------------------------------------------------
# to_vectors
# ---------------------------------------------------------------------------


def test_to_vectors_restricts_year_derived_labels_to_the_name_column() -> None:
    vectors = to_vectors(
        [
            _pair("dates_corroborated"),
            _pair("year_conflict", NON_MATCH),
            _pair("alias_identity", weight=2.5),
            _pair("distinct_musicbrainz", NON_MATCH),
        ]
    )

    assert [v.is_match for v in vectors] == [True, False, True, False]
    assert [v.columns for v in vectors] == [NAME_ONLY, NAME_ONLY, None, None]
    assert vectors[2].weight == 2.5
    assert all(len(v.levels) == len(person_comparisons()) for v in vectors)


# ---------------------------------------------------------------------------
# collect_patterns / sample_non_match_levels / estimate_prior
# ---------------------------------------------------------------------------


def test_collect_patterns_counts_every_candidate_pair_once() -> None:
    records = _records()
    patterns = collect_patterns(records, build_corpus(records))

    assert sum(patterns.values()) == sum(1 for _ in candidate_pairs(records))
    assert all(len(p.levels) == len(person_comparisons()) for p in patterns)


def test_sample_non_match_levels_is_a_distribution_per_column() -> None:
    columns = sample_non_match_levels(_records(), pairs=2_000)

    comparisons = person_comparisons()
    assert len(columns) == len(comparisons)
    for column, comparison in zip(columns, comparisons, strict=True):
        assert set(column) == set(comparison.informative_levels())
        # A column every sampled pair lacked (all ABSENT) stays at zero.
        total = sum(column.values())
        assert total == 0.0 or math.isclose(total, 1.0)


def test_sample_non_match_levels_is_deterministic_for_a_seed() -> None:
    records = _records()
    assert sample_non_match_levels(records, pairs=500) == sample_non_match_levels(records, pairs=500)


def test_sample_non_match_levels_skips_a_record_paired_with_itself() -> None:
    columns = sample_non_match_levels([_record("Bach, J.S.", birth_year=1685)], pairs=100)
    assert all(value == 0.0 for column in columns for value in column.values())


@pytest.mark.usefixtures("small_sample")
def test_estimate_prior_stays_within_its_bounds() -> None:
    records = _records()
    prior = estimate_prior(records, build_corpus(records))
    assert 1e-6 <= prior <= 0.5


# ---------------------------------------------------------------------------
# train, end to end against a warehouse
# ---------------------------------------------------------------------------


def _dates(born: str, died: str) -> tuple[SourceClaim, SourceClaim]:
    return SourceClaim("born_on", value=born), SourceClaim("died_on", value=died)


def _ingest_labellable_people(session: Session) -> None:
    """A warehouse in which every labelling rule fires at least once."""
    first = FakeSource(
        records=(
            person("Bach, J.S.", *_dates("1685", "1750")),
            person("Bach, Johann Sebastian", *_dates("1685-03-31", "1750-07-28")),
            person("Bach, Johann Christian", SourceClaim("born_on", value="1735")),
            person("Mozart, Leopold", SourceClaim("musicbrainz_id", value="mb-leopold")),
            person("Mozart, Wolfgang Amadeus", SourceClaim("musicbrainz_id", value="mb-wolfgang")),
            person("Beethoven, Ludwig van", SourceClaim("also_known_as", value="Louis van Beethoven")),
            person("Louis van Beethoven"),
            *(person(f"Smith, Filler{i}") for i in range(20)),
        ),
        name="first",
    )
    # A second source backing J.S. Bach's dates is what makes them corroborated.
    second = FakeSource(records=(person("Bach, J.S.", *_dates("1685", "1750")),), name="second")
    ingest_source(session, first)
    ingest_source(session, second)


@pytest.mark.usefixtures("small_sample")
def test_train_labels_the_warehouse_and_fits_on_the_train_split(session: Session) -> None:
    _ingest_labellable_people(session)

    result = train(session)

    assert {p.provenance for p in result.labelled} == {
        "dates_corroborated",
        "year_conflict",
        "distinct_musicbrainz",
        "alias_identity",
    }
    # The split partitions the labels: nothing dropped, nothing on both sides.
    assert sorted(result.train + result.test, key=repr) == sorted(result.labelled, key=repr)
    assert not set(result.train) & set(result.test)

    model = result.model
    assert model.trained_on_pairs == len(result.train)
    assert 1e-6 <= model.prior <= 0.5
    for comparison in model.comparisons:
        assert math.isclose(sum(comparison.m.values()), 1.0)
        assert math.isclose(sum(comparison.u.values()), 1.0)


@pytest.mark.usefixtures("small_sample")
def test_train_on_an_empty_warehouse_still_returns_a_usable_model(session: Session) -> None:
    result = train(session)

    assert result.labelled == result.train == result.test == []
    assert result.model.trained_on_pairs == 0
    # With nothing to measure, every column falls back to its smoothed uniform.
    for comparison in result.model.comparisons:
        assert len(set(comparison.m.values())) == 1
