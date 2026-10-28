"""Genre roll-up in the consumer API (#349): a genre stands for itself and every
genre below it. Each test runs against silver and against the promoted gold
copy (SQLite, and Postgres with ``COMPOSER_TEST_POSTGRES_URL`` set)."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from composer_api import create_app
from composer_gold import gold_engine, promote
from composer_models.db import init_db
from composer_models.normalize import entity_uuid, qid_key
from composer_models.testing import pg_url as pg_url  # noqa: F401 - fixture
from composer_models.testing import requires_postgres
from composer_schema import EntityDocument, SourceClaim, WorkMentionDocument
from composer_warehouse.derived import refresh_derived
from composer_warehouse.testing import FakeSource, genre, ingest_source, perf_mention, person
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

DANCE = entity_uuid("genre", qid_key("Q316930"))
WALTZ = entity_uuid("genre", qid_key("Q215"))
OPERA = entity_uuid("genre", qid_key("Q1344"))
CYCLE_A = entity_uuid("genre", qid_key("Q901"))
CYCLE_B = entity_uuid("genre", qid_key("Q902"))
BAROQUE = entity_uuid("movement", qid_key("Q8361"))


def _tag(qid: str, label: str, predicate: str = "has_genre", kind: str = "genre") -> SourceClaim:
    return SourceClaim(predicate, kind, label, object_wikidata_id=qid)


COMPOSERS = (
    "Strauss, Johann",
    "Lehár, Franz",
    "Verdi, Giuseppe",
    "Lanner, Joseph",
    "Cyclist, A",
    "Bach, Johann Sebastian",
)


def _composer(name: str, *claims: SourceClaim) -> EntityDocument:
    return person(name, *claims, external_id=f"p:{name}")


def _programmed(name: str) -> tuple[WorkMentionDocument, ...]:
    """Three works, each on a concert of its own, so /v1/composers lists the composer."""
    return tuple(
        perf_mention(f"{name}:{title}", title, name, {"concert_id": f"{name}:{title}", "date": "1990-01-01"})
        for title in ("Overture", "Serenade", "Requiem")
    )


def _seed(session: Session) -> None:
    ingest_source(
        session,
        FakeSource(
            records=(
                genre("Q2", "Viennese waltz", ("Q215", "waltz")),
                genre("Q215", "waltz", ("Q316930", "dance music")),
                genre("Q316930", "dance music"),
                genre("Q1344", "opera"),
                # P279 has cycles; the roll-up must still terminate
                genre("Q901", "cycle a", ("Q902", "cycle b")),
                genre("Q902", "cycle b", ("Q901", "cycle a")),
                genre("Q8361", "Baroque music", kind="movement"),
            ),
            name="wikidata_genres",
        ),
    )
    records = (
        _composer("Strauss, Johann", _tag("Q2", "Viennese waltz")),
        _composer("Lehár, Franz", _tag("Q215", "waltz"), _tag("Q1344", "opera")),
        _composer("Verdi, Giuseppe", _tag("Q1344", "opera")),
        # an old bronze run: the genre by label only, linked by refresh_derived
        _composer("Lanner, Joseph", SourceClaim("has_genre", "genre", "Waltz")),
        _composer("Cyclist, A", _tag("Q901", "cycle a")),
        _composer("Bach, Johann Sebastian", _tag("Q8361", "Baroque music", "in_movement", "movement")),
    )
    ingest_source(session, FakeSource(records=records, name="wikidata"))
    programmes = tuple(m for name in COMPOSERS for m in _programmed(name))
    ingest_source(session, FakeSource(records=programmes, name="berlinphil"))
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


def _labels(page: dict[str, Any]) -> list[str]:
    return [item["label"] for item in page["items"]]


def test_a_person_tagged_only_with_waltz_is_found_under_dance_music(client: TestClient) -> None:
    page = client.get(f"/v1/genres/{DANCE}/people").json()
    assert _labels(page) == ["Lanner, Joseph", "Lehár, Franz", "Strauss, Johann"]
    assert page["total"] == 3


def test_the_genre_filter_on_composers_rolls_up_too(client: TestClient) -> None:
    page = client.get("/v1/composers", params={"genre": str(DANCE)}).json()
    assert _labels(page) == ["Lanner, Joseph", "Lehár, Franz", "Strauss, Johann"]
    assert _labels(client.get("/v1/composers", params={"genre": str(OPERA), "q": "verdi"}).json()) == [
        "Verdi, Giuseppe"
    ]


def test_facets_are_the_top_level_genres_with_subtree_person_counts(client: TestClient) -> None:
    facets = client.get("/v1/genres").json()
    assert [(f["label"], f["wikidata_id"], f["person_count"]) for f in facets] == [
        ("dance music", "Q316930", 3),
        ("opera", "Q1344", 2),
    ]
    movements = client.get("/v1/genres", params={"kind": "movement"}).json()
    assert [(m["label"], m["kind"], m["person_count"]) for m in movements] == [
        ("Baroque music", "movement", 1)
    ]


def test_genre_detail_lists_parents_and_children(client: TestClient) -> None:
    waltz = client.get(f"/v1/genres/{WALTZ}").json()
    assert waltz["wikidata_id"] == "Q215"
    assert [p["label"] for p in waltz["parents"]] == ["dance music"]
    # gold keeps no genre nobody reaches, so Viennese waltz is the child in both
    assert [c["label"] for c in waltz["children"]] == ["Viennese waltz"]
    assert waltz["person_count"] == 3


def test_a_cycle_in_the_hierarchy_terminates(client: TestClient) -> None:
    assert _labels(client.get(f"/v1/genres/{CYCLE_B}/people").json()) == ["Cyclist, A"]
    assert client.get(f"/v1/genres/{CYCLE_A}").json()["person_count"] == 1


def test_unknown_genre_is_404(client: TestClient) -> None:
    missing = entity_uuid("genre", qid_key("Q404"))
    assert client.get(f"/v1/genres/{missing}").status_code == 404
    assert client.get(f"/v1/genres/{missing}/people").status_code == 404


def test_a_person_id_is_not_a_genre(client: TestClient) -> None:
    person_id = client.get("/v1/entities", params={"q": "Verdi", "kind": "person"}).json()["items"][0]["id"]
    assert client.get(f"/v1/genres/{person_id}").status_code == 404
