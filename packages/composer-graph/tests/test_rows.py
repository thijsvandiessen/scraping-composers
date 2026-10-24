"""Tests for reading gold out as graph rows."""

from pathlib import Path
from typing import Any

from composer_graph.rows import (
    RelGroup,
    Skipped,
    claim_relationships,
    composed_by,
    concert_nodes,
    entity_nodes,
    performed_in,
    programmed,
    work_nodes,
)
from graph_fixture import gold_session
from sqlalchemy.orm import Session


def _labels(gold: Session) -> dict[str, str]:
    """node id → its label or title, so assertions can name nodes."""
    names: dict[str, str] = {row["id"]: row["label"] for _, row in entity_nodes(gold)}
    names.update({row["id"]: row["title"] for row in work_nodes(gold)})
    return names


def _named(gold: Session, rels: list[tuple[RelGroup, dict[str, Any]]]) -> set[tuple[Any, str, Any]]:
    names = _labels(gold)
    return {
        (names.get(row["from"], row["from"]), group.type, names.get(row["to"], row["to"]))
        for group, row in rels
    }


def test_entities_become_labelled_nodes_with_their_literal_claims(session: Session, tmp_path: Path) -> None:
    with gold_session(session, tmp_path) as gold:
        nodes = {row["label"]: (label, row) for label, row in entity_nodes(gold)}

    label, beinum = nodes["Beinum, Eduard van"]
    assert label == "Person"
    assert beinum["born_on"] == "1900-09-03"
    assert beinum["program_count"] == 12  # a count Cypher can order by
    assert beinum["performs_as"] == ["Piano"]  # "piano" is the same instrument
    assert "statement_count" not in beinum  # bookkeeping, not carried over
    assert beinum["sources"] == ["archive"]
    assert nodes["Arnhem"][0] == "Place"
    assert nodes["conductor"][0] == "Profession"
    # the QID a node is matched on in Cypher; absent (not null) without one
    assert nodes["dance music"][0] == "Genre"
    assert nodes["dance music"][1]["wikidata_id"] == "Q316930"
    assert "wikidata_id" not in beinum
    assert all(value is not None for _, row in nodes.values() for value in row.values())


def test_concerts_and_works_are_nodes(session: Session, tmp_path: Path) -> None:
    with gold_session(session, tmp_path) as gold:
        concerts = list(concert_nodes(gold))
        works = {row["title"] for row in work_nodes(gold)}

    assert {concert["date"] for concert in concerts} == {"1929-06-30", "1930-01-05", "1929-07-01"}
    assert all(concert["source"] == "archive" for concert in concerts)
    assert works == {"Symphony No. 5, Op. 67", "Symphony No. 3, Op. 97", "Violin Concerto, Op. 61"}


def test_works_point_at_their_composers(session: Session, tmp_path: Path) -> None:
    skipped = Skipped()
    with gold_session(session, tmp_path) as gold:
        edges = _named(gold, list(composed_by(gold, skipped)))

    assert edges == {
        ("Symphony No. 5, Op. 67", "COMPOSED_BY", "Beethoven, Ludwig van"),
        ("Violin Concerto, Op. 61", "COMPOSED_BY", "Beethoven, Ludwig van"),
        ("Symphony No. 3, Op. 97", "COMPOSED_BY", "Schumann, Robert"),
    }
    assert skipped.orphan_works == 0


def test_performers_link_to_events_with_their_role(session: Session, tmp_path: Path) -> None:
    with gold_session(session, tmp_path) as gold:
        rels = list(performed_in(gold, Skipped()))

    by_role = {row["props"]["role"] for _, row in rels}
    assert by_role == {"conductor", "soloist"}
    assert {(group.start, group.end) for group, _ in rels} == {("Person", "Concert")}
    soloist = next(row for _, row in rels if row["props"]["role"] == "soloist")
    assert soloist["props"]["discipline"] == "violin"
    assert len(rels) == 4  # Beinum ×2, Mengelberg, Zimmermann


def test_events_program_the_works_their_mentions_resolved_to(session: Session, tmp_path: Path) -> None:
    skipped = Skipped()
    with gold_session(session, tmp_path) as gold:
        rels = list(programmed(gold, skipped))

    assert {(group.start, group.end) for group, _ in rels} == {("Concert", "Work")}
    assert len(rels) == 4
    assert all(row["props"]["times"] == 1 for _, row in rels)
    assert skipped.unmatched_mentions == 0


def test_object_claims_become_typed_relationships(session: Session, tmp_path: Path) -> None:
    with gold_session(session, tmp_path) as gold:
        rels = list(claim_relationships(gold, Skipped()))
        edges = _named(gold, rels)

    assert ("Beinum, Eduard van", "BORN_IN", "Arnhem") in edges
    assert ("Beinum, Eduard van", "HAS_PROFESSION", "conductor") in edges
    assert ("Beinum, Eduard van", "STUDENT_OF", "Mengelberg, Willem") in edges
    assert ("Beinum, Eduard van", "HAS_GENRE", "waltz") in edges
    assert ("waltz", "SUBCLASS_OF", "dance music") in edges
    subclass = next(group for group, _ in rels if group.type == "SUBCLASS_OF")
    assert (subclass.start, subclass.end) == ("Genre", "Genre")
    born_in = next(row for group, row in rels if group.type == "BORN_IN")
    assert born_in["props"]["sources"] == ["archive"]
