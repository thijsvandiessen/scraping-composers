"""Tests for scoring a person pair and classifying the result.

Scores are posterior probabilities, so the assertions are about ordering and
which side of a calibrated cut-point a pair falls on, not about magic numbers.
"""

import pytest
from composer_warehouse.persons.extract import parse_name
from composer_warehouse.persons.fellegi_sunter import TermFrequencyTable
from composer_warehouse.persons.match import (
    AUTO_THRESHOLD,
    REVIEW_THRESHOLD,
    Corpus,
    PersonProfile,
    PersonScorer,
    classify,
    default_model,
    score,
)


def _profile(label: str, born: int | None = None, died: int | None = None) -> PersonProfile:
    return PersonProfile(parse_name(label), birth_year=born, death_year=died)


def _scorer(**surname_counts: int) -> PersonScorer:
    """A scorer over a corpus with the given surname frequencies."""
    corpus = Corpus(
        surnames=TermFrequencyTable.from_counts(surname_counts or {"x": 1}),
        given_names=TermFrequencyTable.from_counts({}),
    )
    return PersonScorer(default_model(), corpus)


def test_scores_are_probabilities() -> None:
    value, _ = score(_profile("Bach, Johann Sebastian"), _profile("Bach, Johann Sebastian"))
    assert 0.0 <= value <= 1.0


def test_a_different_surname_is_never_scored_as_a_match() -> None:
    value, _ = score(_profile("Bach, Johann Sebastian"), _profile("Handel, George Frideric"))
    assert classify(value) == "distinct"


def test_conflicting_given_names_lose_to_compatible_ones() -> None:
    scorer = _scorer(jordan=3, smith=400)
    conflict, _ = scorer.score(_profile("Jordan, Jules"), _profile("Jordan, Julius"))
    initials, _ = scorer.score(_profile("Jordan, J."), _profile("Jordan, Julius"))
    exact, _ = scorer.score(_profile("Jordan, Julius"), _profile("Jordan, Julius"))
    assert conflict < initials < exact


def test_initials_alone_do_not_reach_the_auto_threshold() -> None:
    # The #173 defect: this scored exactly AUTO_THRESHOLD under the old rule.
    value, method = _scorer(bach=2, smith=400).score(
        _profile("Bach, J.S."), _profile("Bach, Johann Sebastian")
    )
    assert method == "given:initials"
    assert value < AUTO_THRESHOLD


def test_corroborating_dates_lift_a_pair_over_the_auto_threshold() -> None:
    scorer = _scorer(bach=2, smith=400)
    bare, _ = scorer.score(_profile("Bach, J.S."), _profile("Bach, Johann Sebastian"))
    dated, method = scorer.score(
        _profile("Bach, J.S.", 1685, 1750), _profile("Bach, Johann Sebastian", 1685, 1750)
    )
    assert dated > bare
    assert classify(dated) == "auto_linked"
    assert method == "given:initials+born:exact+died:exact"


def test_a_generation_between_lifetimes_marks_different_people() -> None:
    # Identical names, a lifetime apart: the father, not the son.
    scorer = _scorer(smith=400, strauss=40)
    same, _ = scorer.score(_profile("Strauss, Johann"), _profile("Strauss, Johann"))
    apart, method = scorer.score(_profile("Strauss, Johann", 1804), _profile("Strauss, Johann", 1899))
    assert apart < same
    assert classify(apart) == "distinct"
    assert method == "given:exact+born:conflict"


def test_a_lifetime_conflict_on_a_very_rare_surname_still_never_auto_links() -> None:
    """Evidence is weighed, not vetoed, so a surname only two people in the
    corpus share can pull a year conflict back up into the review queue. It
    cannot pull it over the auto threshold, which is what matters: a reviewed
    pair stays two entities until a human says otherwise."""
    scorer = _scorer(smith=400, sonnenfeld=2)
    value, method = scorer.score(_profile("Sonnenfeld, Johann", 1804), _profile("Sonnenfeld, Johann", 1899))
    assert method == "given:exact+born:conflict"
    assert classify(value) != "auto_linked"


def test_a_rare_shared_surname_outweighs_a_common_one() -> None:
    """The structural gap the issue names: sharing "Sonnenfeld" is far stronger
    evidence than sharing "Smith", and the old scorer weighted them the same."""
    scorer = _scorer(smith=400, sonnenfeld=2)
    common, _ = scorer.score(_profile("Smith, John"), _profile("Smith, John"))
    rare, _ = scorer.score(_profile("Sonnenfeld, John"), _profile("Sonnenfeld, John"))
    assert rare > common


def test_aliases_are_tried_alongside_the_primary_names() -> None:
    scorer = _scorer(beethoven=2, smith=400)
    with_alias = PersonProfile(
        parse_name("Beethoven, Ludwig van"),
        birth_year=1770,
        aliases=(parse_name("Beethoven, Louis van"),),
    )
    plain = _profile("Beethoven, Louis van", 1770)
    without_alias = _profile("Beethoven, Ludwig van", 1770)
    assert scorer.score(with_alias, plain)[0] > scorer.score(without_alias, plain)[0]


def test_an_alias_is_only_compared_against_a_matching_surname() -> None:
    """Alias lists contain arbitrary surnames, and comparing given names across
    two unrelated ones is meaningless.

    Blocking guarantees the *primary* names share a surname, not the aliases,
    so without an explicit gate "Bela Balazs" matched the transliteration alias
    "B. V. Asafev" on initials — two different people who also happen to share
    a birth and death year, which auto-linked them.
    """
    scorer = _scorer(balazs=2, asafev=2, smith=400)
    balazs = PersonProfile(parse_name("Bela Balazs"), 1884, (parse_name("Balash B."),), 1949)
    asafev = PersonProfile(
        parse_name("Boris Asafev"),
        1884,
        (parse_name("B. V. Asafev"), parse_name("Asafev B.")),
        1949,
    )
    value, method = scorer.score(balazs, asafev)
    assert method == "surname_gate"
    assert value == 0.0
    assert classify(value) == "distinct"


def test_a_shared_alias_surname_is_still_compared() -> None:
    # The gate must not throw away the alias matching it exists to enable.
    scorer = _scorer(schoenberg=2, smith=400)
    a = PersonProfile(parse_name("Schoenberg, Arnold"), 1874, (parse_name("Schonberg, Arnold"),), 1951)
    b = PersonProfile(parse_name("Schonberg, Arnold"), 1874, death_year=1951)
    value, method = scorer.score(a, b)
    assert method.startswith("given:exact")
    assert classify(value) == "auto_linked"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.0, "auto_linked"),
        (AUTO_THRESHOLD, "auto_linked"),
        (REVIEW_THRESHOLD, "needs_review"),
        (REVIEW_THRESHOLD - 0.01, "distinct"),
        (0.0, "distinct"),
    ],
)
def test_classify_cut_points(value: float, expected: str) -> None:
    assert classify(value) == expected


def test_the_shipped_model_is_loadable_and_calibrated() -> None:
    model = default_model()
    assert 0.0 < model.prior < 0.1  # matches are a small minority of blocked pairs
    given = model.by_name("given")
    # The level ordering must be reflected in the fitted weights, or the
    # comparison levels do not mean what they claim to.
    assert given.bits("CONFLICT") < given.bits("INITIALS") < given.bits("EXACT")
    for column in ("birth_year", "death_year"):
        year = model.by_name(column)
        assert year.bits("CONFLICT") < 0 < year.bits("EXACT")
        assert year.bits("ABSENT") == 0.0  # no year is no evidence
