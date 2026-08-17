"""Folding stated scoring onto queryable categories — and refusing to guess.

The point of the layer is the question "which works are for piano", so every case
here is really about whether that question would be answered correctly.
"""

from __future__ import annotations

import pytest
from composer_extract.instrumentation import CATEGORIES, CONTAINS, parse_instrumentation


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("piano", ("piano",)),
        ("Piano solo", ("piano",)),
        ("for piano", ("piano",)),
        ("Klavier", ("piano",)),
        ("Cembalo", ("harpsichord",)),
        ("Flöte", ("flute",)),
        ("for string orchestra", ("string orchestra",)),
        ("Streichorchester", ("string orchestra",)),
        ("gemischter Chor", ("mixed choir",)),
    ],
)
def test_a_named_scoring_folds_onto_its_category(raw: str, expected: tuple[str, ...]) -> None:
    """Casing, the qualifiers "for"/"solo", and the German spelling all reach the
    same entity — that is the whole reason the edge exists next to the literal."""
    assert parse_instrumentation(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Violin and Piano", ("violin and piano", "violin", "piano")),
        ("Violine und Klavier", ("violin and piano", "violin", "piano")),
        ("Klavier zu vier Händen", ("piano four hands", "piano")),
        ("Piano 4 hands", ("piano four hands", "piano")),
        ("two pianos", ("two pianos", "piano")),
        ("string quartet", ("string quartet", "violin", "viola", "cello")),
    ],
)
def test_a_combination_also_yields_its_parts(raw: str, expected: tuple[str, ...]) -> None:
    """A violin sonata has to answer "works for piano" as well as "works for
    violin and piano", so the composite leads and its parts follow."""
    assert parse_instrumentation(raw) == expected


def test_an_unnamed_combination_is_read_from_its_conjunction() -> None:
    """Not every pairing is worth a table entry; "and" is enough to read one."""
    assert parse_instrumentation("mixed choir and orchestra") == ("mixed choir", "orchestra")


@pytest.mark.parametrize(
    "raw",
    [
        # An orchestral scoring list. Splitting on the commas would read a
        # symphony as a work for flute, and "strings" here is a section of the
        # orchestra, not a string orchestra.
        "flute, 2 oboes, 2 clarinets, 2 bassoons, 2 horns, 2 trumpets, timpani, strings",
        "2.2.2.2 - 4.2.3.1 - timp - str",
        # Half a conjunction understood is worse than none: this is not a work
        # for piano alone.
        "piano and continuo",
        "Urtext Edition, paperbound",
        "",
        "   ",
    ],
)
def test_an_unrecognised_scoring_yields_nothing_rather_than_a_guess(raw: str) -> None:
    """The stated text survives as the ``orchestration`` literal either way, so
    guessing here would only add a claim the page never made."""
    assert parse_instrumentation(raw) == ()


def test_every_contained_category_is_itself_a_category() -> None:
    """A part nobody declared would create an entity under a label the synonym
    table cannot reach, so the same scoring would split across two entities."""
    parts = {part for parts in CONTAINS.values() for part in parts}
    assert parts <= set(CATEGORIES)


def test_every_containing_category_is_itself_a_category() -> None:
    assert set(CONTAINS) <= set(CATEGORIES)


def test_no_spelling_denotes_two_categories() -> None:
    """A synonym listed under two categories would silently resolve to whichever
    was declared last."""
    spellings = [s for canonical, ss in CATEGORIES.items() for s in (canonical, *ss)]
    assert len(spellings) == len(set(spellings))
