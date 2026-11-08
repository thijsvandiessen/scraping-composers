"""The ensemble endpoints (#350): type, home city and Wikidata id, in silver
(every spelling, folded onto its anchor) and in gold (one ensemble per anchor).
Gold runs on a SQLite file and, with ``COMPOSER_TEST_POSTGRES_URL`` set, on
Postgres."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from composer_api import create_app
from composer_gold import gold_engine, promote
from composer_models.db import init_db
from composer_models.testing import pg_url as pg_url  # noqa: F401 - fixture
from composer_models.testing import requires_postgres
from composer_schema import SourceClaim
from composer_warehouse.concerts import derive_concerts
from composer_warehouse.ensembles import anchor_ensembles
from composer_warehouse.testing import FakeSource, ensemble, ingest_source, perf_mention
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

CREDITS = ("Wiener Philharmoniker", "Vienna Philharmonic", "Junge Philharmonie Wien", "Arditti Quartet")


def _seeded_factory() -> sessionmaker[Session]:
    factory = init_db(
        create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    )
    wikidata = FakeSource(
        records=(
            ensemble(
                "Vienna Philharmonic",
                SourceClaim("ensemble_type", value="orchestra"),
                SourceClaim("based_in", "place", "Vienna"),
                SourceClaim("also_known_as", value="Wiener Philharmoniker"),
                SourceClaim("musicbrainz_id", value="mb-vpo"),
                external_id="Q900001",
                url="https://www.wikidata.org/wiki/Q900001",
            ),
        ),
        name="wikidata_ensembles",
    )
    berlinphil = FakeSource(
        records=(
            *(
                perf_mention(
                    f"perf:{i}",
                    "Symphony No. 9",
                    "Beethoven, Ludwig van",
                    {"concert_id": str(i), "date": f"20{10 + i}-01-01", "ensembles": [name]},
                )
                for i, name in enumerate(CREDITS)
            ),
            # one more concert for the youth orchestra, so it sorts first
            perf_mention(
                "perf:9",
                "Symphony No. 5",
                "Beethoven, Ludwig van",
                {"concert_id": "9", "date": "2030-01-01", "ensembles": ["Junge Philharmonie Wien"]},
            ),
            *(ensemble(name, external_id=f"bp:{name}") for name in CREDITS),
        ),
        name="berlinphil",
    )
    with factory() as s:
        ingest_source(s, wikidata)
        ingest_source(s, berlinphil)
        anchor_ensembles(s)
        derive_concerts(s)
    return factory


@pytest.fixture
def silver() -> TestClient:
    factory = _seeded_factory()
    return TestClient(create_app("test-silver", lambda: factory))


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=requires_postgres)])
def gold(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[TestClient]:
    url = (
        f"sqlite:///{tmp_path / 'gold.db'}"
        if request.param == "sqlite"
        else request.getfixturevalue("pg_url")
    )
    with _seeded_factory()() as s:
        promote(s, url)
    engine = gold_engine(url)
    factory = init_db(engine)
    yield TestClient(create_app("test-gold", lambda: factory))
    engine.dispose()


def _items(client: TestClient, **params: str) -> list[dict[str, Any]]:
    return client.get("/v1/ensembles", params={"limit": "100", **params}).json()["items"]


def _by_label(client: TestClient, **params: str) -> dict[str, dict[str, Any]]:
    """By label — silver holds the anchor and a spelling under the same one,
    so this keeps the name-keyed entities only there."""
    return {i["label"]: i for i in _items(client, **params) if i["wikidata_id"] is None}


def _anchor(client: TestClient) -> dict[str, Any]:
    (anchor,) = (i for i in _items(client) if i["wikidata_id"] == "Q900001")
    return anchor


def test_silver_lists_every_spelling_with_the_anchors_type_and_city(silver: TestClient) -> None:
    anchor = _anchor(silver)
    assert anchor["ensemble_type"] == "orchestra"
    assert anchor["home_city"]["label"] == "Vienna"
    for spelling in ("Wiener Philharmoniker", "Vienna Philharmonic"):
        assert _by_label(silver)[spelling]["canonical_entity_id"] == anchor["id"]
    folded = _by_label(silver)["Wiener Philharmoniker"]
    assert folded["canonical_entity_id"] == anchor["id"]
    assert folded["wikidata_id"] is None
    # read off the anchor it was folded onto
    assert (folded["ensemble_type"], folded["home_city"]) == ("orchestra", anchor["home_city"])
    assert folded["concert_count"] == 1


def test_an_unanchored_ensemble_is_typed_from_its_name(silver: TestClient) -> None:
    items = _by_label(silver)
    assert items["Arditti Quartet"]["ensemble_type"] == "chamber_ensemble"
    assert items["Arditti Quartet"]["home_city"] is None


def test_anchored_false_lists_the_rest_most_credited_first(silver: TestClient) -> None:
    page = silver.get("/v1/ensembles", params={"anchored": "false", "sort": "appearances"}).json()
    assert [i["label"] for i in page["items"]] == ["Junge Philharmonie Wien", "Arditti Quartet"]
    assert page["total"] == 2


def test_anchored_true_and_type_filter(silver: TestClient) -> None:
    anchored = [i["label"] for i in _items(silver, anchored="true")]
    assert sorted(anchored) == ["Vienna Philharmonic", "Vienna Philharmonic", "Wiener Philharmoniker"]
    typed = silver.get("/v1/ensembles", params={"type": "orchestra"}).json()
    assert typed["total"] == 3  # the anchor and its two spellings


def test_silver_detail_has_aliases_musicbrainz_and_claims(silver: TestClient) -> None:
    detail = silver.get(f"/v1/ensembles/{_anchor(silver)['id']}").json()
    # Wikidata's alias and the folded spellings, minus the label itself
    assert detail["aliases"] == ["Wiener Philharmoniker"]
    assert detail["musicbrainz_id"] == "mb-vpo"
    assert detail["part_of"] == []
    assert {c["predicate"] for c in detail["claims"]} >= {"ensemble_type", "based_in"}


def test_gold_has_one_ensemble_per_anchor(gold: TestClient) -> None:
    assert "Wiener Philharmoniker" not in _by_label(gold)
    anchor = _anchor(gold)
    assert (anchor["ensemble_type"], anchor["home_city"]["label"]) == ("orchestra", "Vienna")
    assert anchor["concert_count"] == 2  # both spellings' concerts
    assert anchor["canonical_entity_id"] is None
    assert gold.get(f"/v1/ensembles/{anchor['id']}").json()["label"] == "Vienna Philharmonic"


def test_gold_anchored_false_is_the_qidless_ones(gold: TestClient) -> None:
    assert set(_by_label(gold, anchored="false")) == {"Junge Philharmonie Wien", "Arditti Quartet"}


def test_a_missing_or_non_ensemble_id_is_404(silver: TestClient) -> None:
    assert silver.get("/v1/ensembles/00000000-0000-0000-0000-000000000000").status_code == 404
    place = silver.get("/v1/entities", params={"kind": "place"}).json()["items"][0]
    assert silver.get(f"/v1/ensembles/{place['id']}").status_code == 404


def test_an_unknown_sort_is_422(silver: TestClient) -> None:
    assert silver.get("/v1/ensembles", params={"sort": "random"}).status_code == 422
