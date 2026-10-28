"""Snapshotting each entity's connections from a real Neo4j into gold.

Skipped unless ``COMPOSER_TEST_NEO4J_URL`` points at a server; the load
empties the database it is pointed at. What the API makes of a snapshot is
tested in the consumer API, through the same queries.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from composer_graph import load_graph, snapshot_connections
from composer_models import ConnectionsPayload, Entity, EntityConnections
from graph_fixture import gold_session
from neo4j import Driver, GraphDatabase
from sqlalchemy import select
from sqlalchemy.orm import Session

NEO4J_URL = os.environ.get("COMPOSER_TEST_NEO4J_URL") or None

pytestmark = pytest.mark.skipif(NEO4J_URL is None, reason="COMPOSER_TEST_NEO4J_URL not set")


@pytest.fixture
def driver() -> Iterator[Driver]:
    assert NEO4J_URL is not None
    user = os.environ.get("COMPOSER_TEST_NEO4J_USER", "neo4j")
    password = os.environ.get("COMPOSER_TEST_NEO4J_PASSWORD", "composers")
    with GraphDatabase.driver(NEO4J_URL, auth=(user, password)) as driver:
        yield driver


def _payload(gold: Session, label: str) -> ConnectionsPayload:
    entity_id = gold.scalars(select(Entity.id).where(Entity.label == label)).one()
    row = gold.get(EntityConnections, entity_id)
    assert row is not None
    return cast(ConnectionsPayload, row.payload)


def test_snapshot_stores_each_entitys_edges(session: Session, tmp_path: Path, driver: Driver) -> None:
    with gold_session(session, tmp_path) as gold:
        load_graph(gold, driver)
        stats = snapshot_connections(gold, driver, workers=2)
        beinum = _payload(gold, "Beinum, Eduard van")

    assert stats.snapshots > 0
    performed = {e["label"]: e for e in beinum["edges"] if e["family"] == "performed"}
    assert {label: e["weight"] for label, e in performed.items()} == {
        "Schumann, Robert": 2,
        "Beethoven, Ludwig van": 1,
    }
    schumann = performed["Schumann, Robert"]
    assert schumann["scores"]["weight"] == 2.0
    assert schumann["roles"] == ["conductor"]
    # the same work on two nights is one snippet per year, newest first
    assert schumann["via"] == ["Symphony No. 3, Op. 97 (1930)", "Symphony No. 3, Op. 97 (1929)"]
    # a claim edge carries every relation and the direction of each
    claims = {e["label"]: e for e in beinum["edges"] if e["family"] == "claims"}
    assert claims["Mengelberg, Willem"]["directions"] == {"student_of": "out"}
    assert beinum["weights"]["performed"] == {"1": 1, "2": 1}


def test_snapshot_replaces_the_previous_one(session: Session, tmp_path: Path, driver: Driver) -> None:
    with gold_session(session, tmp_path) as gold:
        load_graph(gold, driver)
        # a profession has no connections (has_profession is left out), so a
        # row for one can only be left over from an earlier snapshot
        conductor = gold.scalars(select(Entity.id).where(Entity.kind == "profession")).one()
        gold.add(EntityConnections(entity_id=conductor, payload={"edges": [], "weights": {}}))
        gold.commit()
        stats = snapshot_connections(gold, driver, workers=1)
        rows = gold.scalars(select(EntityConnections.entity_id)).all()

    assert len(rows) == stats.snapshots
    assert conductor not in rows
