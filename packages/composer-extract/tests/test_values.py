"""Literal coercion: page wording in, the claims table's conventions out."""

from __future__ import annotations

import pytest
from composer_extract.values import coerce_value


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("December 5, 1919", "1919-12-05"),
        # The LA Phil states the premiere and its cast in one line.
        ("December 5, 1919, Walter Henry Rothwell conducting", "1919-12-05"),
        ("5 December 1919", "1919-12-05"),
        ("Dec. 5, 1919", "1919-12-05"),
        ("1st January 1900", "1900-01-01"),
        ("1919-12-05", "1919-12-05"),
        # Precision is preserved rather than invented, as wikidata's dates are.
        ("March 1919", "1919-03"),
        ("1919-03", "1919-03"),
        ("1919", "1919"),
    ],
)
def test_dates_become_iso_at_the_precision_stated(raw: str, expected: str) -> None:
    assert coerce_value("first_performed_on", raw) == expected


@pytest.mark.parametrize("raw", ["sometime in the spring", "between 1804 and 1806"])
def test_an_unparseable_date_is_kept_verbatim(raw: str) -> None:
    """A claim reading "between 1804 and 1806" is worth more than a wrong date."""
    assert coerce_value("born_on", raw) == raw


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("c. 42 minutes", "42"),
        ("42 minutes", "42"),
        ("42 min", "42"),
        ("approximately 8'", "8"),
        ("1 hour 15 minutes", "75"),
        ("2h 5m", "125"),
        ("42", "42"),
    ],
)
def test_durations_become_whole_minutes(raw: str, expected: str) -> None:
    assert coerce_value("duration_minutes", raw) == expected


def test_an_unparseable_duration_is_kept_verbatim() -> None:
    assert coerce_value("duration_minutes", "varies by performance") == "varies by performance"


def test_a_single_year_is_extracted_but_a_range_is_not() -> None:
    """Which end of "1804-1806" the work was composed in is not this layer's
    judgement to make, so the range survives as written."""
    assert coerce_value("composed_in", "composed in 1806") == "1806"
    assert coerce_value("composed_in", "1804-1806") == "1804-1806"


def test_predicates_without_a_convention_pass_through_stripped() -> None:
    assert coerce_value("orchestration", "  flute, 2 oboes  ") == "flute, 2 oboes"
    assert coerce_value("tempo_marking", "Allegro ma non troppo") == "Allegro ma non troppo"
