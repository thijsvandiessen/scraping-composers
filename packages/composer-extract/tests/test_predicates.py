"""The vocabulary layer: what a coined predicate is folded onto, and what is
refused outright."""

from __future__ import annotations

import pytest
from composer_extract.predicates import (
    DENYLIST,
    OBJECT_KINDS,
    VOCABULARY,
    is_known,
    literal_form,
    normalize_predicate,
    object_kind_for,
    slugify,
    takes_literal,
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
        # A publisher's catalogue: the edition credits and the handles on a piece.
        ("Editor", "edited_by"),
        ("Herausgeber", "edited_by"),
        ("Fingering", "fingering_by"),
        ("Publisher", "published_by"),
        ("Besetzung", "orchestration"),
        ("Level of difficulty", "difficulty_level"),
        ("HN", "catalogue_number"),
        ("Order no.", "catalogue_number"),
        ("Opus number", "catalogue_number"),
        ("Key", "in_key"),
        ("Urtext", "edition_type"),
        ("Librettist", "text_by"),
        ("Number of pages", "page_count"),
        ("studied with", "student_of"),
    ],
)
def test_aliases_fold_onto_the_vocabulary(raw: str, expected: str) -> None:
    assert normalize_predicate(raw) == expected
    assert is_known(expected)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("has_scoring", "orchestration"), ("has_duration", "duration_minutes"), ("composed_by", "composed")],
)
def test_the_hand_written_scrapers_spellings_fold_onto_the_same_terms(raw: str, expected: str) -> None:
    """The boosey adapter names three facts differently. Without these aliases one
    work described by both boosey and a crawl of its publisher would land on two
    predicates that never line up on the entity."""
    assert normalize_predicate(raw) == expected


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


def test_no_declared_object_kind_belongs_to_an_uncurated_predicate() -> None:
    """A declaration on a predicate outside the vocabulary would never be
    consulted — ``takes_literal`` only trusts declarations for curated terms."""
    assert set(OBJECT_KINDS) <= VOCABULARY


@pytest.mark.parametrize(
    ("predicate", "kind"),
    [
        ("written_for", "instrumentation"),
        ("published_by", "publisher"),
        ("edited_by", "person"),
        ("member_of", "ensemble"),
        ("arrangement_of", "work"),
    ],
)
def test_a_declared_predicate_decides_its_own_object_kind(predicate: str, kind: str) -> None:
    """The kind comes from the predicate, not from the slot a local model happened
    to fill — it will hand back an entity where a literal belongs and vice versa."""
    assert object_kind_for(predicate) == kind
    assert not takes_literal(predicate)


@pytest.mark.parametrize("predicate", ["catalogue_number", "in_key", "ismn", "difficulty_level"])
def test_an_edition_fact_is_a_literal(predicate: str) -> None:
    assert object_kind_for(predicate) is None
    assert takes_literal(predicate)


def test_the_prompt_hint_is_stable_and_lists_every_term() -> None:
    hint = vocabulary_hint()
    assert hint == vocabulary_hint()  # a moving hint would churn every cache key
    assert set(hint.split(", ")) == VOCABULARY
