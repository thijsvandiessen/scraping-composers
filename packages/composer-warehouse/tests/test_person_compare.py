"""Tests for reducing a person pair to comparison levels.

The given-name cases are the regression suite for #173: the old scorer
compared initials *instead of* the spelled-out given names, so any two names
sharing a first letter agreed.
"""

import pytest
from composer_warehouse.persons.compare import GivenLevel, YearLevel, given_level, year_level
from composer_warehouse.persons.extract import parse_name


def _given(a: str, b: str) -> GivenLevel:
    return given_level(parse_name(a), parse_name(b))


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Jordan, Jules", "Jordan, Julius"),
        ("Clarke, Cuthbert", "Clarke, Campbell"),
        ("Wall, Harry", "Wall, Howard"),
        ("Hamilton, Mark", "Hamilton, Mary Christian Dundas"),
        ("Schnabel, Artur", "Schnabel, Alexander Maria"),
    ],
)
def test_spelled_out_given_names_that_differ_conflict(a: str, b: str) -> None:
    # Every one of these auto-linked under the old `initials` rule because both
    # sides reduced to the same first letter. Two spelled-out tokens must match
    # whole or they conflict.
    assert _given(a, b) is GivenLevel.CONFLICT


def test_initials_against_spelled_out_names_are_compatible() -> None:
    assert _given("Bach, J.S.", "Bach, Johann Sebastian") is GivenLevel.INITIALS
    assert _given("Bach, J. S.", "Bach, Johann Sebastian") is GivenLevel.INITIALS


def test_initials_that_disagree_conflict() -> None:
    assert _given("Bach, J.S.", "Bach, Carl Philipp Emanuel") is GivenLevel.CONFLICT


def test_an_abbreviation_must_prefix_the_name_it_faces() -> None:
    # The issue notes its prototype still admitted "Lee, Ed. S." <-> "Lee, Ella"
    # because both reduce to the initial "e". Requiring the short token to be a
    # prefix of the long one rejects it, while still accepting the abbreviation
    # it was meant to catch.
    assert _given("Lee, Ed. S.", "Lee, Ella") is GivenLevel.CONFLICT
    assert _given("Lee, Ed. S.", "Lee, Edward Stephen") is GivenLevel.INITIALS


def test_a_short_token_that_is_not_a_prefix_conflicts() -> None:
    assert _given("Bach, Jan", "Bach, Janice") is GivenLevel.CONFLICT  # 3 chars: too risky


def test_identical_given_names_are_exact() -> None:
    assert _given("Beethoven, Ludwig van", "Ludwig van Beethoven") is GivenLevel.EXACT


def test_extra_given_names_are_a_prefix_not_an_exact_match() -> None:
    assert _given("Bach, Johann", "Bach, Johann Sebastian") is GivenLevel.PREFIX


def test_missing_given_names_are_absent_not_a_conflict() -> None:
    assert _given("Beethoven", "Beethoven, Ludwig van") is GivenLevel.ABSENT
    assert _given("Beethoven", "Beethoven") is GivenLevel.ABSENT


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (1685, 1685, YearLevel.EXACT),
        (1685, 1686, YearLevel.CLOSE),
        (1685, 1687, YearLevel.CLOSE),
        (1685, 1690, YearLevel.DISTANT),
        (1685, 1715, YearLevel.CONFLICT),  # a father, not a son
        (1685, None, YearLevel.ABSENT),
        (None, None, YearLevel.ABSENT),
    ],
)
def test_year_levels(a: int | None, b: int | None, expected: YearLevel) -> None:
    assert year_level(a, b) is expected
