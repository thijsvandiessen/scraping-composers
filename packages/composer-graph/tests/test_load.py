"""Loading gold into a real Neo4j.

Skipped unless ``COMPOSER_TEST_NEO4J_URL`` points at a server (CI's
``test-neo4j`` job, or ``docker compose up -d neo4j`` locally with
``bolt://localhost:7688``). The load empties the database it is pointed at.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import LiteralString

import pytest
from composer_graph import load_graph
from graph_fixture import gold_session
from neo4j import Driver, GraphDatabase
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


def _rows(driver: Driver, query: LiteralString) -> list[dict[str, object]]:
    records, _, _ = driver.execute_query(query)
    return [record.data() for record in records]


def test_load_builds_the_event_graph(session: Session, tmp_path: Path, driver: Driver) -> None:
    with gold_session(session, tmp_path) as gold:
        stats = load_graph(gold, driver, batch_size=2)  # small batches: exercise the batching

    assert stats.nodes["Concert"] == 3
    assert stats.nodes["Work"] == 3
    assert stats.relationships["PERFORMED_IN"] == 4
    assert stats.relationships["PROGRAMMED"] == 4
    assert stats.relationships["COMPOSED_BY"] == 3
    counts = _rows(driver, "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY label")
    assert {row["label"]: row["n"] for row in counts}["Person"] == stats.nodes["Person"]


def test_performer_to_composer_strength_is_a_path_count(
    session: Session, tmp_path: Path, driver: Driver
) -> None:
    with gold_session(session, tmp_path) as gold:
        load_graph(gold, driver)

    rows = _rows(
        driver,
        "MATCH (p:Person {label: 'Beinum, Eduard van'})-[:PERFORMED_IN]->(e)-[:PROGRAMMED]->(:Work)"
        "-[:COMPOSED_BY]->(c:Person) RETURN c.label AS composer, count(DISTINCT e) AS events "
        "ORDER BY composer",
    )
    assert rows == [
        {"composer": "Beethoven, Ludwig van", "events": 1},
        {"composer": "Schumann, Robert", "events": 2},
    ]


def test_composers_are_connected_by_shared_programmes(
    session: Session, tmp_path: Path, driver: Driver
) -> None:
    with gold_session(session, tmp_path) as gold:
        load_graph(gold, driver)

    rows = _rows(
        driver,
        "MATCH (a:Person)<-[:COMPOSED_BY]-(:Work)<-[:PROGRAMMED]-(e:Concert)-[:PROGRAMMED]->(:Work)"
        "-[:COMPOSED_BY]->(b:Person) WHERE a.label < b.label "
        "RETURN a.label AS a, b.label AS b, count(DISTINCT e) AS concerts",
    )
    assert rows == [{"a": "Beethoven, Ludwig van", "b": "Schumann, Robert", "concerts": 1}]


def test_a_second_load_replaces_the_first(session: Session, tmp_path: Path, driver: Driver) -> None:
    with gold_session(session, tmp_path) as gold:
        load_graph(gold, driver)
        load_graph(gold, driver)

    assert _rows(driver, "MATCH (c:Concert) RETURN count(c) AS n") == [{"n": 3}]


def test_load_stores_each_family_of_reach_separately(
    session: Session, tmp_path: Path, driver: Driver
) -> None:
    """Beethoven is played at c1 and c3, Schumann at c1 and c2; Beinum conducts
    c1 (two works) and c2 (one), and is linked by claims to Arnhem, his teacher
    Mengelberg and the waltz genre. Each count stays in its own property."""
    with gold_session(session, tmp_path) as gold:
        load_graph(gold, driver)

    rows = _rows(
        driver,
        "MATCH (n:Person|Place) RETURN n.label AS label, n.times_performed AS times_performed, "
        "n.performances AS performances, n.appearances AS appearances, n.claim_degree AS claim_degree "
        "ORDER BY label",
    )
    by_label = {row.pop("label"): row for row in rows}
    assert by_label["Beethoven, Ludwig van"]["times_performed"] == 2
    assert by_label["Schumann, Robert"]["times_performed"] == 2
    assert by_label["Beinum, Eduard van"] == {
        "times_performed": None,
        "performances": 3,
        "appearances": 2,
        "claim_degree": 3,
    }
    assert by_label["Zimmermann, Louis"]["performances"] == 1
    assert by_label["Arnhem"]["claim_degree"] == 1
