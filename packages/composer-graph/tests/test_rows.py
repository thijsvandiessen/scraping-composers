"""Tests for reading gold out as graph rows."""

from pathlib import Path
from typing import Any

from composer_gold import promote
from composer_graph.rows import (
    RelGroup,
    Skipped,
    claim_relationships,
    composed_by,
    concert_nodes,
    entity_nodes,
    performed_in,
    programmed,
    work_links,
    work_nodes,
)
from composer_models.db import init_db
from composer_schema import SourceClaim
from composer_warehouse.concerts import derive_concerts
from composer_warehouse.testing import FakeSource, ensemble, ingest_source, mention, perf_mention
from composer_warehouse.works.links import link_works
from graph_fixture import gold_session
from sqlalchemy import create_engine
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


def test_works_carry_identifiers_and_link_to_their_parent(session: Session, tmp_path: Path) -> None:
    bach = "Bach, Johann Sebastian"
    concerto = "Brandenburg Concerto No. 3, BWV 1048"
    ingest_source(
        session,
        FakeSource(
            records=(
                perf_mention("Q1:Q1339", concerto, bach, {"item": "http://www.wikidata.org/entity/Q1"}),
                mention("Allegro (from Brandenburg Concerto No. 3, BWV 1048)", bach, "m2"),
            ),
            name="wikidata_works",
        ),
    )
    link_works(session)
    promote(session, f"sqlite:///{tmp_path / 'gold.db'}")
    with init_db(create_engine(f"sqlite:///{tmp_path / 'gold.db'}"))() as gold:
        works = {row["title"]: row for row in work_nodes(gold)}
        edges = _named(gold, list(work_links(gold, Skipped())))

    assert works[concerto]["identifiers"] == ["BWV 1048", "wikidata Q1"]
    assert works[concerto]["wikidata_id"] == "Q1"
    assert "identifiers" not in works["Allegro (from Brandenburg Concerto No. 3, BWV 1048)"]
    assert edges == {("Allegro (from Brandenburg Concerto No. 3, BWV 1048)", "PART_OF", concerto)}


def test_an_ensemble_carries_its_type_qid_and_home_city(session: Session, tmp_path: Path) -> None:
    """#350: ensemble type as a property, the Wikidata id on the node, and
    BASED_IN to the home city."""
    wikidata = FakeSource(
        records=(
            ensemble(
                "Vienna Philharmonic",
                SourceClaim("ensemble_type", value="orchestra"),
                SourceClaim("based_in", "place", "Vienna"),
                SourceClaim("also_known_as", value="Wiener Philharmoniker"),
                external_id="Q900001",
                url="https://www.wikidata.org/wiki/Q900001",
            ),
        ),
        name="wikidata_ensembles",
    )
    berlinphil = FakeSource(
        records=tuple(
            perf_mention(
                f"perf:{i}",
                "Symphony No. 9",
                "Beethoven, Ludwig van",
                {"concert_id": str(i), "date": f"202{i}-01-01", "ensembles": ["Wiener Philharmoniker"]},
            )
            for i in range(2)
        ),
        name="berlinphil",
    )
    ingest_source(session, wikidata)
    ingest_source(session, berlinphil)
    derive_concerts(session)
    promote(session, f"sqlite:///{tmp_path / 'gold.db'}")
    gold = init_db(create_engine(f"sqlite:///{tmp_path / 'gold.db'}"))()

    nodes = {row["label"]: (label, row) for label, row in entity_nodes(gold)}
    label, orchestra = nodes["Vienna Philharmonic"]
    assert label == "Ensemble"
    assert (orchestra["ensemble_type"], orchestra["wikidata_id"]) == ("orchestra", "Q900001")
    assert "wikidata_id" not in nodes["Vienna"][1]
    edges = _named(gold, list(claim_relationships(gold, Skipped())))
    assert ("Vienna Philharmonic", "BASED_IN", "Vienna") in edges
