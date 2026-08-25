"""Tests for the linkage engine: weights, term frequency, and fitting."""

import math
from pathlib import Path

import pytest
from composer_warehouse.persons.fellegi_sunter import (
    Comparison,
    LabelledVector,
    LinkageModel,
    TermFrequencyTable,
    estimate_prior_by_moments,
    fit_supervised,
    log_odds,
    probability,
)


def _comparison() -> Comparison:
    return Comparison(
        name="given",
        levels=("CONFLICT", "ABSENT", "EXACT"),
        null_level="ABSENT",
        m={"CONFLICT": 0.1, "EXACT": 0.9},
        u={"CONFLICT": 0.9, "EXACT": 0.1},
    )


def approx(value: float, tol: float = 1e-9) -> object:
    return pytest.approx(value, abs=tol)


def test_probability_and_log_odds_round_trip() -> None:
    for value in (0.01, 0.5, 0.9, 0.99):
        assert probability(log_odds(value)) == approx(value)


def test_weights_are_the_log2_bayes_factor() -> None:
    comparison = _comparison()
    assert comparison.bits("EXACT") == approx(math.log2(0.9 / 0.1))
    assert comparison.bits("CONFLICT") == approx(math.log2(0.1 / 0.9))


def test_the_null_level_contributes_nothing() -> None:
    # One side having no given name is an absence of evidence, not evidence.
    assert _comparison().bits("ABSENT") == 0.0


def test_match_weight_sums_prior_and_columns() -> None:
    model = LinkageModel(comparisons=(_comparison(),), prior=0.02)
    expected = log_odds(0.02) + math.log2(0.9 / 0.1)
    assert model.match_weight(["EXACT"]) == approx(expected)
    assert model.match_probability(["EXACT"]) == approx(probability(expected))


def test_term_frequency_rewards_rare_values_and_penalises_common_ones() -> None:
    table = TermFrequencyTable.from_counts({"smith": 100, "sonnenfeld": 2, "jones": 90})
    assert table.bits("sonnenfeld") > 0 > table.bits("smith")
    assert table.bits("unseen-surname") == 0.0  # no basis for an adjustment


def test_term_frequency_averages_to_zero_over_the_pairs_it_scores() -> None:
    # The adjustment redistributes evidence between rare and common values; it
    # must not hand every pair a constant bonus, which would silently move the
    # prior. Weighted by the pairs each value generates, it sums to zero.
    counts = {f"name{i}": i for i in range(2, 40)}
    table = TermFrequencyTable.from_counts(counts, max_bits=99.0)
    weights = {value: count * (count - 1) / 2 for value, count in counts.items()}
    mean = sum(table.bits(v) * w for v, w in weights.items()) / sum(weights.values())
    assert mean == approx(0.0, tol=1e-9)


def test_term_frequency_excludes_over_large_blocks_from_the_reference() -> None:
    # A block the caller will never score must not drag the reference with it.
    counts = {"real": 10, "other": 12, "garbage": 5000}
    with_cap = TermFrequencyTable.from_counts(counts, max_count=100)
    without = TermFrequencyTable.from_counts(counts)
    assert with_cap.reference < without.reference
    assert with_cap.frequencies["garbage"] == without.frequencies["garbage"]  # still scoreable


def test_prior_by_moments_recovers_a_known_mixture() -> None:
    # Build observed = L*m + (1-L)*u from a known L, then recover L. The bound
    # is exact when m puts no mass on the disagreement level.
    u = {"CONFLICT": 0.98, "EXACT": 0.02}
    m = {"CONFLICT": 0.0, "EXACT": 1.0}
    lam = 0.05
    observed = {level: lam * m[level] + (1 - lam) * u[level] for level in u}
    assert estimate_prior_by_moments(observed, u, ("CONFLICT", "EXACT")) == approx(lam, tol=1e-6)


def test_fit_supervised_counts_levels_per_outcome() -> None:
    comparison = Comparison(name="given", levels=("CONFLICT", "EXACT"))
    vectors = [LabelledVector(("EXACT",), is_match=True) for _ in range(100)]
    vectors += [LabelledVector(("CONFLICT",), is_match=False) for _ in range(100)]
    model = fit_supervised(vectors, (comparison,), prior=0.02)
    fitted = model.by_name("given")
    assert fitted.m["EXACT"] > fitted.m["CONFLICT"]
    assert fitted.u["CONFLICT"] > fitted.u["EXACT"]
    assert fitted.bits("EXACT") > 0 > fitted.bits("CONFLICT")


def test_a_row_restricted_to_one_column_does_not_inform_another() -> None:
    # A label derived from birth years must not set the birth-year weights.
    given = Comparison(name="given", levels=("CONFLICT", "EXACT"))
    birth = Comparison(name="birth_year", levels=("CONFLICT", "EXACT"))
    vectors = [LabelledVector(("EXACT", "EXACT"), is_match=True, columns=("given",)) for _ in range(500)]
    model = fit_supervised(vectors, (given, birth), prior=0.02)
    assert model.by_name("given").m["EXACT"] > 0.9
    # birth saw nothing, so smoothing leaves it flat and weightless
    assert model.by_name("birth_year").m["EXACT"] == approx(0.5)
    assert model.by_name("birth_year").bits("EXACT") == approx(0.0)


def test_model_round_trips_through_json(tmp_path: Path) -> None:
    model = LinkageModel(comparisons=(_comparison(),), prior=0.0198, trained_on_pairs=17)
    path = tmp_path / "model.json"
    model.dump(path)
    loaded = LinkageModel.load(path)
    assert loaded.prior == model.prior
    assert loaded.trained_on_pairs == 17
    assert loaded.by_name("given").m == model.by_name("given").m
    assert loaded.match_weight(["EXACT"]) == model.match_weight(["EXACT"])
