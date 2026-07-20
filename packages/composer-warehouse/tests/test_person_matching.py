"""Tests for scoring person-name pairs and classifying the result."""

from composer_warehouse.persons.extract import parse_name
from composer_warehouse.persons.match import (
    AUTO_THRESHOLD,
    REVIEW_THRESHOLD,
    PersonProfile,
    classify,
    score,
)


def _score(a: str, b: str, ya: int | None = None, yb: int | None = None) -> tuple[float, str]:
    return score(PersonProfile(parse_name(a), ya), PersonProfile(parse_name(b), yb))


def test_initials_compatible_auto_links() -> None:
    value, method = _score("Bach, J.S.", "Bach, Johann Sebastian")
    assert method == "initials"
    assert classify(value) == "auto_linked"


def test_exact_given_names_auto_link() -> None:
    value, _ = _score("Beethoven, Ludwig van", "Ludwig van Beethoven")
    assert classify(value) == "auto_linked"


def test_surname_only_goes_to_review() -> None:
    value, method = _score("Beethoven", "Beethoven, Ludwig van")
    assert method == "surname_only"
    assert classify(value) == "needs_review"
    assert REVIEW_THRESHOLD <= value < AUTO_THRESHOLD


def test_different_given_names_are_distinct() -> None:
    value, method = _score("Strauss, Johann", "Strauss, Richard")
    assert method == "given_conflict"
    assert classify(value) == "distinct"


def test_different_surname_never_matches() -> None:
    value, _ = _score("Bach, Johann Sebastian", "Handel, George Frideric")
    assert value == 0.0


def test_birth_year_conflict_overrides_name_similarity() -> None:
    # same name + matching initials, but lifetimes a century apart -> not the same
    value, method = _score("Strauss, Johann", "Strauss, Johann", 1804, 1825)
    assert method == "year_conflict"
    assert classify(value) == "distinct"


def test_matching_birth_year_boosts_a_review_into_auto() -> None:
    bare, _ = _score("Bach", "Bach, Johann Sebastian")
    assert classify(bare) == "needs_review"
    boosted, _ = _score("Bach", "Bach, Johann Sebastian", 1685, 1685)
    assert classify(boosted) == "auto_linked"
