"""Tests for the ``raw["premiere"]`` convention and its precedence rule."""

import pytest
from composer_schema.premiere import date_parts, premiere_of, supersedes


@pytest.mark.parametrize(
    ("date", "parts"),
    [
        ("1824", (1824,)),
        ("1824-05", (1824, 5)),
        ("1824-05-07", (1824, 5, 7)),
        ("-200", (-200,)),
        ("7 May 1824", None),
        ("1824-5-7", None),
        ("", None),
    ],
)
def test_date_parts(date: str, parts: tuple[int, ...] | None) -> None:
    assert date_parts(date) == parts


def test_premiere_of_reads_the_convention() -> None:
    raw = {"premiere": {"date": "1824-05-07", "place": " Vienna "}}
    assert premiere_of(raw) == ("1824-05-07", "Vienna")


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"premiere": "1824-05-07"},
        {"premiere": {"date": "7 May 1824"}},
        {"premiere": {"date": None, "place": "Vienna"}},
        # a house's premiere lives elsewhere and is never read
        {"premieres": {"ROH premiere": {"date": "1876-06-22"}}},
    ],
)
def test_premiere_of_ignores_anything_else(raw: dict[str, object]) -> None:
    assert premiere_of(raw) is None


def test_a_blank_place_is_no_place() -> None:
    assert premiere_of({"premiere": {"date": "1824", "place": "  "}}) == ("1824", None)


@pytest.mark.parametrize(
    ("new", "current", "wins"),
    [
        ("1824-05-07", None, True),
        ("1824-05-07", "1825-01-01", True),  # earlier wins
        ("1825-01-01", "1824-05-07", False),
        ("1824-05-07", "1824", True),  # a refinement wins
        ("1824", "1824-05-07", False),
        ("1824-05-07", "1824-05-07", False),  # nothing to change
        ("1823", "1824-05-07", True),
        ("-200", "100", True),
        ("garbled", None, False),
        ("1824", "garbled", True),
    ],
)
def test_supersedes(new: str, current: str | None, wins: bool) -> None:
    assert supersedes(new, current) is wins
