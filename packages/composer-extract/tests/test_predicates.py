"""The vocabulary layer: what a coined predicate is folded onto, and what is
refused outright."""

from __future__ import annotations

import pytest
from composer_extract.predicates import (
    DENYLIST,
    VOCABULARY,
    is_known,
    literal_form,
    normalize_predicate,
    slugify,
    vocabulary_hint,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Length", "duration_minutes"),
        ("  Composed  ", "composed"),
        ("Year Composed", "composed_in"),
        ("date of composition", "composed_in"),
        ("Instrumentation", "orchestration"),
        ("Date of Birth", "born_on"),
        ("nationality", "citizen_of"),
    ],
)
def test_aliases_fold_onto_the_vocabulary(raw: str, expected: str) -> None:
    assert normalize_predicate(raw) == expected
    assert is_known(expected)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("First LA Phil Performance", "first_la_phil_performance"),
        ("tempo-marking", "tempo_marking"),
        ("Publisher's note", "publisher_s_note"),
    ],
)
def test_a_coined_predicate_survives_but_is_not_known(raw: str, expected: str) -> None:
    """Open extraction is the point: an unrecognised predicate is kept, and the
    caller counts it because ``is_known`` says it is new."""
    assert normalize_predicate(raw) == expected
    assert not is_known(expected)


@pytest.mark.parametrize("predicate", sorted(DENYLIST))
def test_denylisted_predicates_are_refused(predicate: str) -> None:
    """``sitelink_count`` decides who enters gold and ``mentioned_in`` is written
    by the ingest loop; neither may come from a crawled page."""
    assert normalize_predicate(predicate) is None
    assert normalize_predicate(predicate.replace("_", " ").title()) is None


def test_composed_means_the_year_when_its_object_is_a_literal() -> None:
    """ "Composed" heads both "Beethoven composed the Violin Concerto" and the LA
    Phil's "Composed 1806" row; the object is the only thing that separates them."""
    assert normalize_predicate("Composed") == "composed"
    assert literal_form("composed") == "composed_in"
    assert literal_form("has_profession") == "has_profession"


def test_an_empty_predicate_is_refused() -> None:
    assert normalize_predicate("   ") is None
    assert normalize_predicate("!!") is None


def test_slugify_collapses_punctuation_runs() -> None:
    assert slugify("First  --  Performance!!") == "first_performance"


def test_no_alias_target_is_outside_the_vocabulary() -> None:
    """An alias pointing at a term nobody curated would quietly create a second
    name for the same fact — exactly what the layer exists to prevent."""
    from composer_extract.predicates import ALIASES

    assert {target for target in ALIASES.values()} <= VOCABULARY


def test_no_alias_key_is_itself_a_vocabulary_term() -> None:
    """A key that is also a term would rename a predicate the prompt asked for."""
    from composer_extract.predicates import ALIASES

    assert not (set(ALIASES) & VOCABULARY)


def test_the_prompt_hint_is_stable_and_lists_every_term() -> None:
    hint = vocabulary_hint()
    assert hint == vocabulary_hint()  # a moving hint would churn every cache key
    assert set(hint.split(", ")) == VOCABULARY
