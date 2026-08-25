"""Tests for the cannot-links derived from authority identifiers.

Two Wikidata QIDs are two items and usually two people, so the pass must
refuse to merge them — but Wikidata holds duplicate items, so corroboration
has to be able to discharge the constraint. Both halves are pinned here.
"""

import uuid

from composer_warehouse.persons.cluster import Edge
from composer_warehouse.persons.constraints import (
    MUSICBRAINZ,
    WIKIDATA,
    Constraints,
    authority_constraints,
    corroborated,
)
from composer_warehouse.persons.corpus import PersonRecord
from composer_warehouse.persons.extract import parse_name

# Ascending, so the constraint pairs the tests assert on read in id order.
A, B, C = sorted(uuid.uuid4() for _ in range(3))


def _record(entity_id: uuid.UUID, label: str, **kwargs: object) -> PersonRecord:
    return PersonRecord(entity_id=entity_id, label=label, name=parse_name(label), **kwargs)  # type: ignore[arg-type]


def _linked(*records: PersonRecord) -> list[Edge]:
    """Edges joining every record to the first, so all of them share a component."""
    first, *rest = records
    return [Edge(first.entity_id, other.entity_id, 0.99) for other in rest]


def test_distinct_qids_are_a_cannot_link() -> None:
    a = _record(A, "Aaron Aachen", wikidata_ids=frozenset({"Q139217390"}))
    b = _record(B, "Aaron Aachen", wikidata_ids=frozenset({"Q12358097"}))

    constraints = authority_constraints([a, b], _linked(a, b))

    assert constraints.cannot_link == ((A, B),)
    assert constraints.conflicts[0].authorities == (WIKIDATA,)
    assert constraints.discharged == ()


def test_distinct_musicbrainz_ids_are_a_cannot_link() -> None:
    a = _record(A, "Aarne Mannik", musicbrainz_ids=frozenset({"77ffd9e3"}))
    b = _record(B, "Aarne Mannik", musicbrainz_ids=frozenset({"1041a5b2"}))

    constraints = authority_constraints([a, b], _linked(a, b))

    assert constraints.cannot_link == ((A, B),)
    assert constraints.conflicts[0].authorities == (MUSICBRAINZ,)


def test_both_authorities_are_reported_on_one_conflict() -> None:
    a = _record(A, "X", wikidata_ids=frozenset({"Q1"}), musicbrainz_ids=frozenset({"m1"}))
    b = _record(B, "X", wikidata_ids=frozenset({"Q2"}), musicbrainz_ids=frozenset({"m2"}))

    constraints = authority_constraints([a, b], _linked(a, b))

    assert len(constraints.conflicts) == 1
    assert constraints.conflicts[0].authorities == (WIKIDATA, MUSICBRAINZ)


def test_a_shared_id_is_not_a_conflict() -> None:
    a = _record(A, "X", wikidata_ids=frozenset({"Q1"}), musicbrainz_ids=frozenset({"m1"}))
    b = _record(B, "X", wikidata_ids=frozenset({"Q1"}), musicbrainz_ids=frozenset({"m1"}))

    assert authority_constraints([a, b], _linked(a, b)) == Constraints()


def test_one_sided_evidence_is_not_a_conflict() -> None:
    """Only one side carries an id, so no authority has said anything about the
    pair. This is the common case — 80,291 person entities have no QID."""
    a = _record(A, "X", wikidata_ids=frozenset({"Q1"}))
    b = _record(B, "X")

    assert authority_constraints([a, b], _linked(a, b)) == Constraints()


def test_records_the_edges_cannot_join_are_not_compared() -> None:
    """The constraint search is scoped to the edge graph's components.

    Nothing proposes merging two records with no path between them, so a
    conflict there is not a constraint — it is 8.6 billion comparisons the pass
    does not have to make.
    """
    a = _record(A, "X", wikidata_ids=frozenset({"Q1"}))
    b = _record(B, "Y", wikidata_ids=frozenset({"Q2"}))

    assert authority_constraints([a, b], []) == Constraints()


def test_a_conflict_reachable_only_transitively_is_still_a_constraint() -> None:
    """A~C and C~B would merge A with B, and nothing ever scored that pair."""
    a = _record(A, "X", wikidata_ids=frozenset({"Q1"}))
    b = _record(B, "X", wikidata_ids=frozenset({"Q2"}))
    c = _record(C, "X")

    constraints = authority_constraints([a, b, c], [Edge(A, C, 0.99), Edge(C, B, 0.99)])

    assert constraints.cannot_link == ((A, B),)


def test_corroboration_discharges_the_constraint() -> None:
    """The Aaron Aachen case: two QIDs, one person.

    Wikidata records "Aaron Aachen" as Norbert Linke's pseudonym and the birth
    years agree, so the authority has contradicted its own id.
    """
    a = _record(A, "Aaron Aachen", birth_year=1933, wikidata_ids=frozenset({"Q139217390"}))
    b = _record(
        B,
        "Norbert Linke",
        birth_year=1933,
        aliases=(parse_name("Aaron Aachen"),),
        wikidata_ids=frozenset({"Q1796591"}),
    )

    constraints = authority_constraints([a, b], _linked(a, b))

    assert constraints.cannot_link == ()
    assert constraints.discharged == constraints.conflicts
    assert corroborated(a, b)


def test_an_alias_alone_does_not_discharge() -> None:
    """Both halves are required. Wikidata lists exonyms and near-namesakes as
    aliases too, so without a birth year to agree on the id stands."""
    a = _record(A, "Aaron Aachen", wikidata_ids=frozenset({"Q1"}))
    b = _record(B, "Norbert Linke", aliases=(parse_name("Aaron Aachen"),), wikidata_ids=frozenset({"Q2"}))

    assert not corroborated(a, b)
    assert authority_constraints([a, b], _linked(a, b)).cannot_link == ((A, B),)


def test_an_agreeing_birth_year_alone_does_not_discharge() -> None:
    """Two people born the same year look exactly like this."""
    a = _record(A, "Aarne Mannik", birth_year=1947, wikidata_ids=frozenset({"Q139027755"}))
    b = _record(B, "Aarne Mannik", birth_year=1947, wikidata_ids=frozenset({"Q12358097"}))

    assert not corroborated(a, b)
    assert authority_constraints([a, b], _linked(a, b)).cannot_link == ((A, B),)


def test_a_year_apart_still_agrees() -> None:
    """Sources disagree by a year constantly; a duplicate should not turn on
    which of them was read first."""
    a = _record(A, "Aaron Aachen", birth_year=1933, wikidata_ids=frozenset({"Q1"}))
    b = _record(
        B,
        "Norbert Linke",
        birth_year=1934,
        aliases=(parse_name("Aaron Aachen"),),
        wikidata_ids=frozenset({"Q2"}),
    )

    assert corroborated(a, b)


def test_a_decade_apart_does_not_agree() -> None:
    a = _record(A, "Aaron Aachen", birth_year=1933, wikidata_ids=frozenset({"Q1"}))
    b = _record(
        B,
        "Norbert Linke",
        birth_year=1943,
        aliases=(parse_name("Aaron Aachen"),),
        wikidata_ids=frozenset({"Q2"}),
    )

    assert not corroborated(a, b)


def test_the_conflicts_are_ordered_deterministically() -> None:
    """Two runs over the same data must report the same pairs in the same
    order, whatever order the records and edges arrive in."""
    records = [
        _record(A, "X", wikidata_ids=frozenset({"Q1"})),
        _record(B, "X", wikidata_ids=frozenset({"Q2"})),
        _record(C, "X", wikidata_ids=frozenset({"Q3"})),
    ]
    edges = [Edge(A, B, 0.99), Edge(B, C, 0.98)]

    first = authority_constraints(records, edges)
    second = authority_constraints(list(reversed(records)), list(reversed(edges)))

    assert first == second
    assert first.cannot_link == ((A, B), (A, C), (B, C))
