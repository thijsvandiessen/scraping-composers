"""The whole pipeline over synthetic sources: fetch → bucket → process → silver →
dedupe → promote → gold → consumer API, every step through the real CLI.

The unit suites seed silver by hand and promote it in-process; these tests
instead run what an operator runs and check what lands in each tier. Which
names should survive into gold, and why, is laid out in
:mod:`composer_e2e.sources`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from composer_api import create_app
from composer_e2e.harness import SOURCES, Pipeline
from composer_models import Claim, Concert, Entity, PersonMatch, Recording, Work
from composer_models.db import init_db
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, aliased

#: Who gold should hold after the first ingestion event, by kind.
GOLD_PEOPLE = {"Anne-Sophie Mutter", "Gustav Mahler", "Ludwig van Beethoven"}
GOLD_ENSEMBLES = {"Synthetic Philharmonic"}


def _labels(engine: Engine, kind: str) -> list[str]:
    with Session(engine) as session:
        return sorted(session.scalars(select(Entity.label).where(Entity.kind == kind)))


def _count(engine: Engine, model: type[Any]) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def _birthplaces(engine: Engine, label: str) -> set[str]:
    place = aliased(Entity)
    with Session(engine) as session:
        return set(
            session.scalars(
                select(place.label)
                .join(Claim, Claim.object_id == place.id)
                .join(Entity, Entity.id == Claim.subject_id)
                .where(Entity.label == label, Claim.predicate == "born_in")
            )
        )


def _gold_api(pipeline: Pipeline) -> TestClient:
    return TestClient(create_app("gold (e2e)", lambda: init_db(pipeline.gold())))


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory: pytest.TempPathFactory) -> Pipeline:
    """One ingestion event followed by a promote, shared by the read-only tests below."""
    pipeline = Pipeline(tmp_path_factory.mktemp("pipeline"))
    pipeline.ingest()
    pipeline.curate()
    return pipeline


@pytest.fixture(scope="module")
def api(pipeline: Pipeline) -> Iterator[TestClient]:
    with _gold_api(pipeline) as client:
        yield client


# --- bronze ---------------------------------------------------------------------


def test_fetch_writes_one_completed_snapshot_per_source(pipeline: Pipeline) -> None:
    counts: dict[str, int | None] = {}
    for source in SOURCES:
        (run_dir,) = (pipeline.bucket / source).iterdir()
        manifest = json.loads((run_dir / "manifest.json").read_text())
        assert manifest["status"] == "completed"
        lines = (run_dir / "records.ndjson").read_text().splitlines()
        assert manifest["record_count"] == len(lines)
        counts[source] = manifest["record_count"]
    assert counts == {"wikidata": 3, "berlinphil": 10, "decca": 6}


# --- silver ---------------------------------------------------------------------


def test_silver_keeps_every_listed_name(pipeline: Pipeline) -> None:
    # Silver curates nothing: the uncredited, the one-off and both halves of each
    # duplicate pair are all still here.
    people = _labels(pipeline.silver(), "person")
    assert people.count("Ludwig van Beethoven") == 2  # QID twin + programme/label name
    assert people.count("Gustav Mahler") == 2
    assert {"Guest, One-Off", "Nobody, Obscure", "Anne-Sophie Mutter"} <= set(people)
    assert _labels(pipeline.silver(), "ensemble") == ["Listed-Only Consort", "Synthetic Philharmonic"]


def test_dedupe_links_the_qid_twins_from_source_evidence(pipeline: Pipeline) -> None:
    # No review step: the life dates the label publishes corroborate the wikidata
    # item well enough to auto-link, and nothing is left for a human to decide.
    with Session(pipeline.silver()) as session:
        matches = session.scalars(select(PersonMatch)).all()
        assert {m.status for m in matches} == {"auto_linked"}
        assert sorted(m.entity.label for m in matches) == ["Gustav Mahler", "Ludwig van Beethoven"]
        assert all(m.entity.label == m.canonical.label for m in matches)


def test_silver_derives_concerts_and_recordings(pipeline: Pipeline) -> None:
    with Session(pipeline.silver()) as session:
        concerts = session.scalars(select(Concert).order_by(Concert.date)).all()
        assert [c.external_key for c in concerts] == ["c1", "c2", "c3"]
        first = {(p.role, p.name) for p in concerts[0].participants}
        assert first == {
            ("conductor", "Gustav Mahler"),
            ("soloist", "Anne-Sophie Mutter"),
            ("ensemble", "Synthetic Philharmonic"),
        }
        assert all(p.entity_id is not None for c in concerts for p in c.participants)

        recordings = session.scalars(select(Recording).order_by(Recording.external_key)).all()
        assert [(r.external_key, len(r.works)) for r in recordings] == [("p1", 1), ("p2", 2)]


def test_work_mentions_resolve_to_one_work_per_piece(pipeline: Pipeline) -> None:
    # Eight mentions across a concert archive and a label, three distinct pieces.
    with Session(pipeline.silver()) as session:
        titles = sorted(session.scalars(select(Work.canonical_title)))
    assert len(titles) == 3
    assert any("Op. 61" in t for t in titles) and any("Op. 67" in t for t in titles)


# --- gold -----------------------------------------------------------------------


def test_gold_keeps_only_credited_people_once_each(pipeline: Pipeline) -> None:
    assert _labels(pipeline.gold(), "person") == sorted(GOLD_PEOPLE)
    assert _labels(pipeline.gold(), "ensemble") == sorted(GOLD_ENSEMBLES)
    with Session(pipeline.gold()) as session:
        assert session.scalar(select(Entity.id).where(Entity.canonical_entity_id.is_not(None))) is None


def test_gold_collapse_carries_the_duplicate_claims(pipeline: Pipeline) -> None:
    # The birthplace came only from the wikidata twin; it must survive the collapse
    # onto the one Beethoven row gold keeps, and the orphaned place must not.
    assert _birthplaces(pipeline.gold(), "Ludwig van Beethoven") == {"Cologne"}
    assert "Atlantis" not in _labels(pipeline.gold(), "place")


def test_gold_copies_concerts_recordings_and_works(pipeline: Pipeline) -> None:
    gold = pipeline.gold()
    assert (_count(gold, Concert), _count(gold, Recording), _count(gold, Work)) == (3, 2, 3)
    manifest = json.loads((pipeline.root / "gold.db.manifest.json").read_text())
    assert manifest["status"] == "completed"


# --- gold API -------------------------------------------------------------------


def test_api_lists_the_curated_composers(api: TestClient) -> None:
    body = api.get("/v1/composers", params={"limit": 100}).json()
    assert {item["label"] for item in body["items"]} == {"Gustav Mahler", "Ludwig van Beethoven"}


def test_api_composer_detail_cites_every_source(api: TestClient) -> None:
    (beethoven,) = api.get("/v1/composers", params={"q": "Beethoven"}).json()["items"]
    claims = api.get(f"/v1/composers/{beethoven['id']}").json()["claims"]
    assert {c["source"] for c in claims} == {"wikidata", "decca"}
    born_in = [c for c in claims if c["predicate"] == "born_in"]
    assert [(c["object_label"], c["source_external_id"]) for c in born_in] == [("Cologne", "Q255")]


def test_api_concerts_carry_their_resolved_participants(api: TestClient) -> None:
    concerts = api.get("/v1/concerts").json()
    assert concerts["total"] == 3
    first = next(c for c in concerts["items"] if c["date"] == "2024-01-10")
    detail = api.get(f"/v1/concerts/{first['id']}").json()
    assert {(p["role"], p["name"]) for p in detail["participants"] if p["entity_id"]} == {
        ("conductor", "Gustav Mahler"),
        ("soloist", "Anne-Sophie Mutter"),
        ("ensemble", "Synthetic Philharmonic"),
    }
    assert len(detail["works"]) == 2


def test_api_dropped_conductor_stays_on_the_programme_unlinked(api: TestClient) -> None:
    # Rule 1 dropped the one-off conductor from gold's people, but the concert
    # still credits them by name — with no entity link to a row that isn't there.
    third = next(c for c in api.get("/v1/concerts").json()["items"] if c["date"] == "2024-03-20")
    detail = api.get(f"/v1/concerts/{third['id']}").json()
    conductor = next(p for p in detail["participants"] if p["role"] == "conductor")
    assert (conductor["name"], conductor["entity_id"]) == ("Guest, One-Off", None)


def test_api_person_concerts_and_recordings(api: TestClient) -> None:
    (mutter,) = api.get("/v1/soloists", params={"q": "Mutter"}).json()["items"]
    assert (mutter["concert_count"], mutter["recording_count"]) == (2, 2)
    concerts = api.get(f"/v1/people/{mutter['id']}/concerts").json()
    assert {c["role"] for c in concerts["items"]} == {"soloist"}
    recordings = api.get(f"/v1/people/{mutter['id']}/recordings").json()
    assert recordings["total"] == 2


# --- across runs ----------------------------------------------------------------


def test_a_second_ingestion_event_flows_through_to_gold(tmp_path: Path) -> None:
    """A later fetch of the same sources adds a concert and a changed fact; after
    process + promote, gold reflects both — and the new concert lifts the one-off
    conductor over the appearance threshold."""
    pipeline = Pipeline(tmp_path)
    pipeline.ingest(edition=1)
    pipeline.curate()
    assert "Guest, One-Off" not in _labels(pipeline.gold(), "person")

    pipeline.ingest(edition=2)
    pipeline.curate()

    for source in SOURCES:
        assert len(list((pipeline.bucket / source).iterdir())) == 2  # bronze keeps both runs
    assert "Guest, One-Off" in _labels(pipeline.gold(), "person")
    assert _count(pipeline.gold(), Concert) == 4
    # Claims are append-only: the corrected value joins the old one rather than
    # replacing it (pinned at unit level by test_reingest_with_changed_content_updates_record).
    assert _birthplaces(pipeline.gold(), "Ludwig van Beethoven") == {"Cologne", "Bonn"}
    with _gold_api(pipeline) as api:
        assert api.get("/v1/concerts").json()["total"] == 4


def test_rebuild_silver_from_bronze_reproduces_gold(pipeline: Pipeline, tmp_path: Path) -> None:
    """Silver is disposable: replaying the bucket into a fresh database and promoting
    again yields the same gold."""
    rebuilt = Pipeline(tmp_path)
    # Same bucket, fresh databases: rebuild-silver reads the bucket the first run wrote.
    rebuilt.cli("rebuild-silver", "--bucket-path", str(pipeline.bucket))
    rebuilt.curate()

    for kind in ("person", "ensemble", "place", "profession"):
        assert _labels(rebuilt.gold(), kind) == _labels(pipeline.gold(), kind), kind
    for model in (Concert, Recording, Work, Claim):
        assert _count(rebuilt.gold(), model) == _count(pipeline.gold(), model), model.__name__
