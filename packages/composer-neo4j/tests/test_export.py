"""Tests for the export orchestration and the Cypher it emits.

A recording stub stands in for the driver, so the whole export — constraints,
wipe, node writes, relationship writes, manifest — runs without a Neo4j
instance. The live instance is only exercised by ``test_integration``.
"""

from pathlib import Path
from typing import Any

import composer_neo4j.export as export_module
import pytest
from composer_neo4j import (
    AURA_FREE_MAX_RELATIONSHIPS,
    ExportConfig,
    ExportTooLargeError,
    export_to_neo4j,
    read_export_manifest,
)
from composer_neo4j.writer import GraphWriter
from composer_warehouse.concerts import derive_concerts
from composer_warehouse.testing import FakeSource, ingest_source, perf_mention, person
from sqlalchemy.orm import Session

CONFIG = ExportConfig(uri="bolt://x", user="u", password="p")


class FakeDriver:
    """Records statements and answers the two count queries from what it saw.

    Deduplicating ids and (start, type, end) triples the way ``MERGE`` does is
    what lets the tests assert on ``duplicate_edges_merged`` without a server.
    """

    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.node_ids: set[str] = set()
        self.edges: set[tuple[str, str, str]] = set()
        self.closed = False

    def execute_query(self, cypher: str, **params: Any) -> tuple[list[list[int]], None, None]:
        self.statements.append((cypher, params))
        if "count(n)" in cypher:
            return [[len(self.node_ids)]], None, None
        if "count(r)" in cypher:
            return [[len(self.edges)]], None, None
        if "DETACH DELETE" in cypher:
            self.node_ids.clear()
            self.edges.clear()
        rows: list[dict[str, Any]] = params.get("rows", [])
        for row in rows:
            if "id" in row:
                self.node_ids.add(row["id"])
            else:
                self.edges.add((row["start"], _rel_type(cypher), row["end"]))
        return [], None, None

    def session(self, **_kwargs: Any) -> "FakeSession":
        """The auto-commit path the wipe needs (CALL … IN TRANSACTIONS)."""
        return FakeSession(self)

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, driver: FakeDriver) -> None:
        self._driver = driver

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def run(self, cypher: str, **params: Any) -> "FakeResult":
        self._driver.execute_query(cypher, **params)
        return FakeResult()


class FakeResult:
    def consume(self) -> None:
        return None


def _rel_type(cypher: str) -> str:
    return cypher.split("MERGE (a)-[r:`")[1].split("`")[0]


class RecordingWriter(GraphWriter):
    """A GraphWriter over a FakeDriver, so the real writer code path runs."""

    def __init__(self) -> None:
        self.driver = FakeDriver()
        super().__init__(self.driver)

    @property
    def statements(self) -> list[tuple[str, dict[str, Any]]]:
        return self.driver.statements

    def cypher_containing(self, needle: str) -> list[str]:
        return [cypher for cypher, _params in self.statements if needle in cypher]


def _seed(session: Session) -> None:
    source = FakeSource(
        name="archive",
        base_url="https://archive.example",
        records=(
            perf_mention(
                "p1",
                "Symphony No. 5, Op. 67",
                "Beethoven, Ludwig van",
                {
                    "_source": "llm",
                    "concert_key": "concert/1",
                    "date": "1910-01-02",
                    "conductors": ["Mahler, Gustav"],
                },
            ),
            person("Mahler, Gustav", external_id="a:mahler"),
        ),
    )
    ingest_source(session, source)
    derive_concerts(session)


def test_export_writes_constraints_then_wipe_then_data(session: Session, tmp_path: Path) -> None:
    _seed(session)
    writer = RecordingWriter()

    export_to_neo4j(session, CONFIG, manifest_key=tmp_path / "neo4j", writer=writer)

    order = [cypher for cypher, _params in writer.statements]
    first_merge = next(i for i, c in enumerate(order) if "MERGE (n:" in c)
    wipe_at = next(i for i, c in enumerate(order) if "DETACH DELETE" in c)
    last_constraint = max(i for i, c in enumerate(order) if "CREATE CONSTRAINT" in c)

    assert last_constraint < wipe_at < first_merge


def test_export_reports_stats_and_writes_a_manifest(session: Session, tmp_path: Path) -> None:
    _seed(session)
    key = tmp_path / "neo4j"

    stats = export_to_neo4j(session, CONFIG, manifest_key=key, writer=RecordingWriter())

    assert stats.concerts == 1
    assert stats.nodes > 0 and stats.relationships > 0

    manifest = read_export_manifest(key)
    assert manifest is not None
    assert manifest.status == "completed"
    assert manifest.stats["concerts"] == 1


def test_a_failed_export_is_recorded_as_failed(session: Session, tmp_path: Path) -> None:
    """A crashed export must never look like a finished one."""
    _seed(session)
    key = tmp_path / "neo4j"

    class Exploding(RecordingWriter):
        def run(self, cypher: str, **params: Any) -> None:
            raise RuntimeError("instance unreachable")

    with pytest.raises(RuntimeError, match="unreachable"):
        export_to_neo4j(session, CONFIG, manifest_key=key, writer=Exploding())

    manifest = read_export_manifest(key)
    assert manifest is not None
    assert manifest.status == "failed"
    assert "unreachable" in (manifest.error or "")


def test_an_oversized_export_refuses_before_wiping(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The capacity check must run before the wipe, or a refusal still destroys
    the previous graph."""
    _seed(session)
    writer = RecordingWriter()
    monkeypatch.setattr(
        export_module, "count_relationships", lambda *_a, **_k: AURA_FREE_MAX_RELATIONSHIPS + 1
    )

    with pytest.raises(ExportTooLargeError, match="exceeds"):
        export_to_neo4j(session, CONFIG, manifest_key=tmp_path / "neo4j", writer=writer)

    assert writer.statements == []


def test_wipe_can_be_switched_off(session: Session, tmp_path: Path) -> None:
    _seed(session)
    writer = RecordingWriter()
    config = ExportConfig(uri="bolt://x", user="u", password="p", wipe_first=False)

    export_to_neo4j(session, config, manifest_key=tmp_path / "neo4j", writer=writer)

    assert writer.cypher_containing("DETACH DELETE") == []


def test_stats_report_the_graph_not_the_rows_sent(session: Session, tmp_path: Path) -> None:
    """A concert listing the same work twice sends two rows and keeps one edge.

    Reporting rows sent would overstate the graph; the stats read the counts
    back from the target and expose the difference instead.
    """
    source = FakeSource(
        name="archive",
        base_url="https://archive.example",
        records=(
            perf_mention(
                "p1",
                "Symphony No. 5, Op. 67",
                "Beethoven, Ludwig van",
                {"_source": "llm", "concert_key": "c/1", "date": "1910-01-02"},
            ),
            # the same work again on the same programme (an encore)
            perf_mention(
                "p2",
                "Symphony No. 5, Op. 67",
                "Beethoven, Ludwig van",
                {"_source": "llm", "concert_key": "c/1", "date": "1910-01-02"},
            ),
        ),
    )
    ingest_source(session, source)
    derive_concerts(session)

    stats = export_to_neo4j(session, CONFIG, manifest_key=tmp_path / "neo4j", writer=RecordingWriter())

    assert stats.duplicate_edges_merged == 1
    assert stats.rows_sent > stats.nodes + stats.relationships


def test_batching_splits_large_writes(session: Session, tmp_path: Path) -> None:
    _seed(session)
    writer = RecordingWriter()
    config = ExportConfig(uri="bolt://x", user="u", password="p", batch_size=1)

    export_to_neo4j(session, config, manifest_key=tmp_path / "neo4j", writer=writer)

    node_writes = [params["rows"] for cypher, params in writer.statements if "MERGE (n:" in cypher]
    assert node_writes and all(len(rows) == 1 for rows in node_writes)
