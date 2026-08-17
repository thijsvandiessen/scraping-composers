"""Folding stated scoring onto queryable categories — and refusing to guess.

The point of the layer is the question "which works are for piano", so every case
here is really about whether that question would be answered correctly.
"""

from __future__ import annotations

import pytest
from composer_extract.instrumentation import (
    CATEGORIES,
    CONTAINS,
    MEMBERS,
    category_for,
    members_of,
    parse_instrumentation,
)


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
    ],
)
def test_a_combination_also_yields_its_parts(raw: str, expected: tuple[str, ...]) -> None:
    """A violin sonata has to answer "works for piano" as well as "works for
    violin and piano", so the composite leads and its parts follow."""
    assert parse_instrumentation(raw) == expected


@pytest.mark.parametrize(
    ("raw", "members"),
    [
        ("string quartet", ("violin", "viola", "cello")),
        ("Streichquartett", ("violin", "viola", "cello")),
        ("piano trio", ("piano", "violin", "cello")),
    ],
)
def test_an_ensemble_names_only_itself_and_lists_its_members_apart(
    raw: str, members: tuple[str, ...]
) -> None:
    """A quartet is what the work is *for*; the violin is a member of it. Folding
    the two together would return every quartet ever written for "works for
    violin", which is the same dilution an orchestral shorthand would cause."""
    assert parse_instrumentation(raw) == (category_for(raw),)
    assert members_of(parse_instrumentation(raw)) == members


def test_an_instrument_pairing_has_no_members() -> None:
    """ "Violin and piano" names instruments, not an ensemble, so its parts are
    what the work is for and none of them is merely included."""
    assert members_of(parse_instrumentation("Violin and Piano")) == ()


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
        # Publisher shorthand is not this module's job: it belongs to
        # ``shorthand.parse_shorthand``, which the caller tries first.
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


@pytest.mark.parametrize("table", [CONTAINS, MEMBERS])
def test_every_expanded_category_is_itself_a_category(table: dict[str, tuple[str, ...]]) -> None:
    """A part nobody declared would create an entity under a label the synonym
    table cannot reach, so the same scoring would split across two entities."""
    parts = {part for parts in table.values() for part in parts}
    assert parts <= set(CATEGORIES)
    assert set(table) <= set(CATEGORIES)


def test_no_category_is_both_a_pairing_and_an_ensemble() -> None:
    """The two tables decide which predicate a part lands on, so a category in
    both would emit its parts twice under conflicting meanings."""
    assert not (set(CONTAINS) & set(MEMBERS))


def test_no_spelling_denotes_two_categories() -> None:
    """A synonym listed under two categories would silently resolve to whichever
    was declared last."""
    spellings = [s for canonical, ss in CATEGORIES.items() for s in (canonical, *ss)]
    assert len(spellings) == len(set(spellings))
