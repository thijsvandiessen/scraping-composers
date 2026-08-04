"""Tests for the gold → property-graph mapping. No Neo4j instance involved."""

from typing import Any

import pytest
from composer_neo4j import ExportConfig, ExportTooLargeError, check_capacity, count_nodes
from composer_neo4j.export import count_relationships
from composer_neo4j.mapping import (
    build_index,
    iter_concert_nodes,
    iter_entity_nodes,
    iter_recording_nodes,
    iter_work_nodes,
    literal_properties,
)
from composer_neo4j.relationships import (
    iter_claim_relationships,
    iter_composer_relationships,
    iter_participant_relationships,
    iter_programme_relationships,
)
from composer_schema import SourceClaim
from composer_warehouse.concerts import derive_concerts
from composer_warehouse.models import Claim, Entity
from composer_warehouse.recordings import derive_recordings
from composer_warehouse.testing import (
    FakeSource,
    ensemble,
    ingest_source,
    mention,
    perf_mention,
    person,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

CONFIG = ExportConfig(uri="bolt://x", user="u", password="p")


def _seed(session: Session) -> None:
    """A silver database exercising every mapping branch."""
    archive = FakeSource(
        name="archive",
        base_url="https://archive.example",
        records=(
            # a catalogue-only work: no concert, so out of scope by default
            mention("Piano Sonata No. 14, Op. 27", "Beethoven, Ludwig van", "cat1"),
            perf_mention(
                "p1",
                "Symphony No. 5, Op. 67",
                "Beethoven, Ludwig van",
                {
                    "_source": "llm",
                    "concert_key": "concert/1",
                    "date": "1910-01-02",
                    "venue": "Musikverein",
                    "season": "1909/10",
                    "conductors": ["Mahler, Gustav"],
                    "soloists": [{"name": "Schnabel, Artur", "discipline": "piano"}],
                    "ensembles": ["Wiener Philharmoniker"],
                },
            ),
            perf_mention(
                "p2",
                "Symphony No. 7, Op. 92",
                "Beethoven, Ludwig van",
                {
                    "_source": "llm",
                    "concert_key": "concert/1",
                    "date": "1910-01-02",
                    "venue": "Musikverein",
                    "conductors": ["Mahler, Gustav"],
                },
            ),
            # a recording, which is where an ensemble credit can appear: the LLM
            # concert schema has no ensembles field, only the recording one does
            perf_mention(
                "r1",
                "Symphony No. 5, Op. 67",
                "Beethoven, Ludwig van",
                {
                    "_source": "llm",
                    "_kind": "recording",
                    "record_key": "album/1",
                    "title": "Beethoven: Symphony No. 5",
                    "label": "Deutsche Grammophon",
                    "artists": [
                        {"name": "Wiener Philharmoniker", "role": "ensemble"},
                        {"name": "Mahler, Gustav", "role": "conductor"},
                    ],
                },
            ),
            person(
                "Mahler, Gustav",
                SourceClaim("born_in", "place", "Vienna"),
                SourceClaim("born_on", value="1860-07-07"),
                SourceClaim("sitelink_count", value="97"),
                external_id="a:mahler",
            ),
            person("Schnabel, Artur", external_id="a:schnabel"),
            ensemble("Wiener Philharmoniker", external_id="a:wph"),
        ),
    )
    ingest_source(session, archive)
    derive_concerts(session)
    derive_recordings(session)


def _nodes(session: Session, config: ExportConfig = CONFIG) -> dict[str, list[dict[str, Any]]]:
    index = build_index(session, config)
    out: dict[str, list[dict[str, Any]]] = {}
    for batches in (
        iter_entity_nodes(session, index, config),
        iter_work_nodes(session, index, config),
        iter_concert_nodes(session, index, config),
        iter_recording_nodes(session, index, config),
    ):
        for batch in batches:
            out.setdefault(batch.label, []).extend(batch.rows)
    return out


def _rels(session: Session, config: ExportConfig = CONFIG) -> list[tuple[str, str, dict[str, Any]]]:
    index = build_index(session, config)
    found: list[tuple[str, str, dict[str, Any]]] = []
    for batches in (
        iter_claim_relationships(session, index, config),
        iter_composer_relationships(session, index, config),
        iter_participant_relationships(session, index, config),
        iter_programme_relationships(session, index, config),
    ):
        for batch in batches:
            found.extend((batch.type, batch.start_label, row) for row in batch.rows)
    return [(rel_type, start, row) for rel_type, start, row in found]


def test_entities_become_labelled_nodes(session: Session) -> None:
    _seed(session)
    nodes = _nodes(session)

    assert {n["props"]["label"] for n in nodes["Person"]} >= {"Mahler, Gustav"}
    assert {n["props"]["label"] for n in nodes["Ensemble"]} == {"Wiener Philharmoniker"}
    assert {n["props"]["label"] for n in nodes["Place"]} == {"Vienna"}


def test_literal_claims_become_node_properties(session: Session) -> None:
    _seed(session)
    mahler = next(n for n in _nodes(session)["Person"] if n["props"]["label"] == "Mahler, Gustav")

    assert mahler["props"]["born_on"] == "1860-07-07"
    # numeric literals are stored as numbers so Cypher can order by them
    assert mahler["props"]["sitelink_count"] == 97


def test_conflicting_literals_collapse_to_the_most_asserted(session: Session) -> None:
    _seed(session)
    subject = session.scalar(select(Entity.id).where(Entity.label == "Mahler, Gustav"))
    source_id = session.scalar(select(Claim.source_id).where(Claim.subject_id == subject))
    assert subject is not None and source_id is not None
    # two sources say 1860, one says the wrong year
    session.add_all(
        [
            Claim(subject_id=subject, predicate="born_on", value="1860-07-07", source_id=source_id),
            Claim(subject_id=subject, predicate="born_on", value="1861-01-01", source_id=source_id),
        ]
    )
    session.flush()

    assert literal_properties(session)[subject]["born_on"] == "1860-07-07"


def test_object_claims_become_relationships_with_their_source(session: Session) -> None:
    _seed(session)
    born_in = [row for rel, _start, row in _rels(session) if rel == "BORN_IN"]

    assert len(born_in) == 1
    assert born_in[0]["props"]["source"] == "archive"


def test_concerts_are_keyed_by_source_and_external_key(session: Session) -> None:
    _seed(session)
    concerts = _nodes(session)["Concert"]

    assert len(concerts) == 1
    assert concerts[0]["id"] == "archive:concert/1"
    assert concerts[0]["props"]["venue"] == "Musikverein"
    assert concerts[0]["props"]["season"] == "1909/10"
    assert concerts[0]["props"]["source"] == "archive"


def test_participants_share_one_relationship_family(session: Session) -> None:
    """Concerts and recordings emit the same relationship types.

    In the relational schema these are two parallel tables with identical
    columns; here the only difference is the start label, which is the point.
    """
    _seed(session)
    rels = _rels(session)
    by_type = {rel for rel, _start, _row in rels}

    assert {"CONDUCTED_BY", "PERFORMED_BY", "FEATURES"} <= by_type
    starts = {rel: {start for r, start, _row in rels if r == rel} for rel in ("CONDUCTED_BY", "FEATURES")}
    assert starts["CONDUCTED_BY"] == {"Concert", "Recording"}
    assert starts["FEATURES"] == {"Recording"}

    soloist = next(row for rel, _s, row in rels if rel == "PERFORMED_BY")
    assert soloist["props"]["discipline"] == "piano"
    assert soloist["props"]["name"] == "Schnabel, Artur"


def test_programmes_carry_their_position(session: Session) -> None:
    _seed(session)
    positions = sorted(
        row["props"]["position"] for rel, _s, row in _rels(session) if rel == "PROGRAMMES"
    )

    assert positions == [1, 2]


def test_unperformed_works_are_excluded_by_default(session: Session) -> None:
    _seed(session)
    titles = {n["props"]["title"] for n in _nodes(session)["Work"]}

    assert "Piano Sonata No. 14, Op. 27" not in titles
    assert any("Symphony No. 5" in title for title in titles)


def test_unperformed_works_are_included_on_request(session: Session) -> None:
    _seed(session)
    config = ExportConfig(uri="bolt://x", user="u", password="p", include_unperformed_works=True)
    titles = {n["props"]["title"] for n in _nodes(session, config)["Work"]}

    assert "Piano Sonata No. 14, Op. 27" in titles


def test_unresolved_names_survive_as_event_properties(session: Session) -> None:
    """A credit that resolved to no entity must not vanish from the export."""
    source = FakeSource(
        name="archive",
        base_url="https://archive.example",
        records=(
            perf_mention(
                "p1",
                "Some Work",
                "Someone",
                {
                    "_source": "llm",
                    "concert_key": "concert/9",
                    "date": "1999-01-01",
                    "conductors": ["Never Ingested, Person"],
                },
            ),
        ),
    )
    ingest_source(session, source)
    derive_concerts(session)

    concert = _nodes(session)["Concert"][0]
    assert concert["props"]["unresolved_participants"] == ["Never Ingested, Person"]


def test_counts_match_what_the_iterators_produce(session: Session) -> None:
    """The capacity check gates on these counts, so they must not drift."""
    _seed(session)
    index = build_index(session, CONFIG)

    assert count_nodes(index) == sum(len(rows) for rows in _nodes(session).values())
    assert count_relationships(session, index) == len(_rels(session))


def test_capacity_check_refuses_an_oversized_graph() -> None:
    check_capacity(1_000, 1_000)  # comfortably inside the caps

    with pytest.raises(ExportTooLargeError, match="exceeds"):
        check_capacity(500_000, 1_000)
    with pytest.raises(ExportTooLargeError, match="include unperformed works"):
        check_capacity(1_000, 500_000)
