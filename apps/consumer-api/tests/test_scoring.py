"""A work's instrumentation on ``/v1/works/{id}``: read from the source entities
linked to the canonical work. Each test runs against silver and against the
promoted gold copy (SQLite, and Postgres with ``COMPOSER_TEST_POSTGRES_URL`` set)."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from composer_api import create_app
from composer_gold import gold_engine, promote
from composer_models.db import init_db
from composer_models.testing import pg_url as pg_url  # noqa: F401 - fixture
from composer_models.testing import requires_postgres
from composer_schema import EntityDocument, SourceClaim, WorkMentionDocument
from composer_warehouse.derived import refresh_derived
from composer_warehouse.testing import FakeSource, ingest_source, mention
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

BRAHMS = "Brahms, Johannes"
SYMPHONY = "Symphony No. 2 in D major, Op. 73"
CONCERTO = "Violin Concerto in D major, Op. 77"
NOW = datetime(2026, 11, 1, tzinfo=UTC)


def _edge(predicate: str, label: str) -> SourceClaim:
    return SourceClaim(predicate, "instrumentation", label)


def _entity(
    source: str, external_id: str, parts: list[dict[str, Any]], *claims: SourceClaim
) -> EntityDocument:
    return EntityDocument(
        id=external_id,
        url=f"https://{source}.example/{external_id}",
        source_name=source,
        ingested_at=NOW,
        name="Symphony No. 2 (Brahms, Johannes)",
        kind="work",
        raw={"instrumentation": {"parts": parts}},
        claims=claims,
    )


def _mention(source: str, external_id: str) -> WorkMentionDocument:
    return WorkMentionDocument(
        id=external_id,
        url=None,
        source_name=source,
        ingested_at=NOW,
        title=SYMPHONY,
        composer=BRAHMS,
        raw={"item": "http://www.wikidata.org/entity/Q1"},
    )


BAERENREITER_PARTS = [
    {"stated": "Flute (2)", "instrument": "flute", "count": 2},
    {"stated": "Horn (4)", "instrument": "horn", "count": 4},
    {"stated": "Ophicleide", "instrument": None},
]


def _seed(session: Session) -> None:
    # the score and the parts: two editions, one work, the same list
    ingest_source(
        session,
        FakeSource(
            records=tuple(
                document
                for edition in ("BA1", "BA1P")
                for document in (
                    mention(SYMPHONY, BRAHMS, edition),
                    _entity(
                        "baerenreiter",
                        edition,
                        BAERENREITER_PARTS,
                        _edge("written_for", "orchestra"),
                        _edge("includes_instrument", "flute"),
                        _edge("includes_instrument", "horn"),
                    ),
                )
            ),
            name="baerenreiter",
        ),
    )
    ingest_source(
        session,
        FakeSource(
            records=(
                _mention("wikidata_instrumentation", "Q1:Q7294"),
                _entity(
                    "wikidata_instrumentation",
                    "Q1",
                    [{"item": "Q193", "label": "horn", "instrument": "horn", "count": 4}],
                    _edge("includes_instrument", "horn"),
                ),
            ),
            name="wikidata_instrumentation",
        ),
    )
    ingest_source(session, FakeSource(records=(mention(CONCERTO, BRAHMS, "o1"),), name="other"))
    refresh_derived(session)


def _silver() -> sessionmaker[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    factory = init_db(engine)
    with factory() as session:
        _seed(session)
    return factory


@pytest.fixture(params=["silver", "gold", pytest.param("gold-postgres", marks=requires_postgres)])
def client(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[TestClient]:
    silver = _silver()
    if request.param == "silver":
        yield TestClient(create_app("test-silver", lambda: silver))
        return
    gold_url = (
        f"sqlite:///{tmp_path / 'gold.db'}" if request.param == "gold" else request.getfixturevalue("pg_url")
    )
    with silver() as session:
        promote(session, gold_url)
    engine = gold_engine(gold_url)
    gold = init_db(engine)
    yield TestClient(create_app("test-gold", lambda: gold))
    engine.dispose()  # before pg_url drops the schema under it


def _work(client: TestClient, title: str) -> dict[str, Any]:
    (listed,) = [
        w
        for w in client.get("/v1/works", params={"q": title[:10]}).json()["items"]
        if w["canonical_title"] == title
    ]
    return client.get(f"/v1/works/{listed['id']}").json()


def test_a_work_carries_the_instrumentation_its_sources_state(client: TestClient) -> None:
    scoring = _work(client, SYMPHONY)["scoring"]

    assert [(i["label"], i["sources"]) for i in scoring["written_for"]] == [("orchestra", ["baerenreiter"])]
    assert [(i["label"], i["sources"]) for i in scoring["includes"]] == [
        ("flute", ["baerenreiter"]),
        ("horn", ["baerenreiter", "wikidata_instrumentation"]),
    ]
    assert all(i["id"] for i in scoring["written_for"] + scoring["includes"])


def test_each_sources_part_list_keeps_its_counts_once(client: TestClient) -> None:
    sources = _work(client, SYMPHONY)["scoring"]["sources"]

    # the two editions' identical lists are one
    assert [s["source"] for s in sources] == ["baerenreiter", "wikidata_instrumentation"]
    baerenreiter, wikidata = sources
    assert baerenreiter["parts"] == [
        {"stated": "Flute (2)", "instrument": "flute", "count": 2, "solo": False},
        {"stated": "Horn (4)", "instrument": "horn", "count": 4, "solo": False},
        # a name the table does not know is shown as the source stated it
        {"stated": "Ophicleide", "instrument": None, "count": None, "solo": False},
    ]
    assert baerenreiter["source_url"].startswith("https://baerenreiter.example/BA1")
    assert wikidata["parts"] == [{"stated": "horn", "instrument": "horn", "count": 4, "solo": False}]


def test_a_work_no_source_describes_has_an_empty_scoring(client: TestClient) -> None:
    assert _work(client, CONCERTO)["scoring"] == {"written_for": [], "includes": [], "sources": []}
