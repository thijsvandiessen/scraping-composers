# pylint: disable=too-many-lines
"""Admin API tests using an in-memory database and a tmp_path bucket.

The Starlette TestClient runs FastAPI BackgroundTasks synchronously once the
response is returned, so a triggered fetch/load has already finished by the
time the POST call returns here — which lets us assert the completed result.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import composer_admin.build_routes as build_routes
import composer_admin.deps as admin_deps
import composer_admin.routes as admin_routes
import composer_admin.snapshots as admin_snapshots
import pytest
from composer_admin import admin_app
from composer_bronze.bucket import LocalBucket, SnapshotManifest
from composer_schema import (
    EntityDocument,
    RefreshCadence,
    SourceAdapter,
)
from composer_warehouse.db import init_db
from composer_warehouse.ingestion import create_run
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


def _person(name: str) -> EntityDocument:
    return EntityDocument(
        id=f"id:{name}",
        url=None,
        source_name="fake",
        ingested_at=datetime(2024, 1, 1, tzinfo=UTC),
        name=name,
        raw={"id": name},
    )


class _FakeSource(SourceAdapter):
    name = "fake"
    base_url = "https://fake.example"
    cadence = RefreshCadence.MONTHLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument]:
        yield _person("Doe, Jane")
        yield _person("Smith, John")


class _ArchiveSource(SourceAdapter):
    name = "archive"
    base_url = "https://archive.example"
    cadence = RefreshCadence.STATIC

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument]:
        yield _person("Bach, Johann")


class _ExplodingSource(SourceAdapter):
    name = "exploding"
    base_url = "https://exploding.example"
    cadence = RefreshCadence.MONTHLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument]:
        yield _person("Doe, Jane")
        raise RuntimeError("source exploded")


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    return init_db(engine)


@pytest.fixture
def bucket_path(tmp_path: Path) -> Path:
    return tmp_path / "bucket"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, factory, bucket_path: Path) -> Iterator[TestClient]:  # pyright: ignore[reportMissingParameterType]
    monkeypatch.setattr(admin_deps, "_session_factory", factory)
    registry = {"fake": _FakeSource(), "archive": _ArchiveSource(), "exploding": _ExplodingSource()}
    monkeypatch.setattr(admin_routes, "REGISTRY", registry)
    monkeypatch.setattr(admin_snapshots, "REGISTRY", registry)
    monkeypatch.setattr(admin_snapshots, "DEFAULT_BUCKET_PATH", str(bucket_path))
    from composer_config import settings

    monkeypatch.setattr(settings, "admin_api_key", "test-key")
    yield TestClient(admin_app, headers={"X-Admin-Key": "test-key"})


def test_list_scrapers_reports_cadence_and_due(client: TestClient) -> None:
    r = client.get("/admin/v1/scrapers")
    assert r.status_code == 200
    by_name = {s["name"]: s for s in r.json()}
    assert by_name["fake"]["cadence"] == "monthly"
    assert by_name["fake"]["due"] is True  # never fetched, monthly cadence
    assert by_name["fake"]["last_snapshot"] is None
    assert by_name["archive"]["cadence"] == "static"
    assert by_name["archive"]["due"] is False  # static sources are never auto-due


def test_fetch_writes_snapshot_and_manifest(client: TestClient, bucket_path: Path) -> None:
    r = client.post("/admin/v1/scrapers/fake/fetch")
    assert r.status_code == 202
    started = r.json()
    assert started["source"] == "fake"
    snapshot_id = started["snapshot_id"]

    ndjson = bucket_path / "fake" / snapshot_id / "records.ndjson"
    assert len(ndjson.read_text().strip().splitlines()) == 2
    manifest = json.loads((ndjson.parent / "manifest.json").read_text())
    assert manifest["status"] == "completed"
    assert manifest["record_count"] == 2

    # the fetch made the source fresh and is reported as the last snapshot
    fake = next(s for s in client.get("/admin/v1/scrapers").json() if s["name"] == "fake")
    assert fake["due"] is False
    assert fake["last_snapshot"]["id"] == snapshot_id
    assert fake["last_snapshot"]["status"] == "completed"


def test_fetch_conflicts_while_fetch_running(client: TestClient, bucket_path: Path) -> None:
    LocalBucket(bucket_path).write_manifest(SnapshotManifest.start("fake", "2026-01-01T00:00:00-abc"))
    assert client.post("/admin/v1/scrapers/fake/fetch").status_code == 409


def test_fetch_unknown_scraper_404(client: TestClient) -> None:
    assert client.post("/admin/v1/scrapers/nope/fetch").status_code == 404


def test_failed_fetch_records_failed_manifest(client: TestClient, bucket_path: Path) -> None:
    r = client.post("/admin/v1/scrapers/exploding/fetch")
    assert r.status_code == 202
    snapshot_id = r.json()["snapshot_id"]

    manifest = json.loads((bucket_path / "exploding" / snapshot_id / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert "source exploded" in manifest["error"]

    # a failed snapshot is not loadable
    r = client.post(f"/admin/v1/snapshots/exploding/{snapshot_id}/process")
    assert r.status_code == 409
    assert "not loadable" in r.json()["detail"]


def test_fetch_due_starts_only_due_scrapers(client: TestClient) -> None:
    r = client.post("/admin/v1/scrapers/fetch-due")
    assert r.status_code == 200
    started = {s["source"] for s in r.json()}
    assert "fake" in started  # monthly, never fetched -> due
    assert "archive" not in started  # static -> never auto-due


def test_snapshots_lists_newest_first_and_legacy_as_unknown(client: TestClient, bucket_path: Path) -> None:
    # legacy snapshot: records.ndjson without a manifest (pre-manifest bucket)
    legacy = bucket_path / "archive" / "2026-01-01T00:00:00-legacy0"
    legacy.mkdir(parents=True)
    (legacy / "records.ndjson").write_text('{"x": 1}\n')

    client.post("/admin/v1/scrapers/fake/fetch")

    snapshots = client.get("/admin/v1/snapshots").json()
    assert [s["source"] for s in snapshots] == ["fake", "archive"]  # newest first
    assert snapshots[0]["status"] == "completed"
    assert snapshots[0]["kind"] == "documents"  # entity docs are loadable
    assert snapshots[1]["status"] == "unknown"
    assert snapshots[1]["kind"] == "pages"  # legacy record has no document _type
    assert snapshots[1]["size_bytes"] > 0


def test_process_loads_snapshot_into_db(client: TestClient) -> None:
    snapshot_id = client.post("/admin/v1/scrapers/fake/fetch").json()["snapshot_id"]

    r = client.post(f"/admin/v1/snapshots/fake/{snapshot_id}/process")
    assert r.status_code == 202
    run_id = r.json()["run_id"]

    detail = client.get(f"/admin/v1/runs/{run_id}")
    assert detail.status_code == 200
    data = detail.json()
    assert data["status"] == "completed"
    assert data["records_seen"] == 2
    assert data["records_new"] == 2


def test_process_unknown_snapshot_404(client: TestClient) -> None:
    assert client.post("/admin/v1/snapshots/fake/nope/process").status_code == 404
    assert client.post("/admin/v1/snapshots/nope/nope/process").status_code == 404


def _stale_running_snapshot(bucket_path: Path, source: str, snapshot_id: str, records: int) -> None:
    """A snapshot left behind by a killed process: pages on disk, manifest still 'running'."""
    run_dir = bucket_path / source / snapshot_id
    run_dir.mkdir(parents=True)
    (run_dir / "records.ndjson").write_text(
        "".join(f'{{"_type": "crawl", "n": {n}}}\n' for n in range(records))
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source": source,
                "run_id": snapshot_id,
                "status": "running",
                "started_at": "2026-01-01T00:00:00+00:00",
                "finished_at": None,
                "record_count": None,
                "error": None,
            }
        )
    )


def test_abandon_frees_a_source_stuck_on_a_dead_run(client: TestClient, bucket_path: Path) -> None:
    """A killed fetch never finalizes its manifest, so the source stays blocked
    forever; abandoning it is the way back without deleting the pages."""
    _stale_running_snapshot(bucket_path, "fake", "2026-01-01T00:00:00-stale00", records=3)
    assert client.post("/admin/v1/scrapers/fake/fetch").status_code == 409

    r = client.post("/admin/v1/snapshots/fake/2026-01-01T00:00:00-stale00/abandon")

    assert r.status_code == 200
    assert r.json()["status"] == "failed"
    assert r.json()["record_count"] == 3  # corrected to what the killed run had written
    assert "abandoned" in r.json()["error"]
    assert client.post("/admin/v1/scrapers/fake/fetch").status_code == 202


def test_abandon_rejects_a_snapshot_that_is_not_running(client: TestClient) -> None:
    snapshot_id = client.post("/admin/v1/scrapers/fake/fetch").json()["snapshot_id"]

    r = client.post(f"/admin/v1/snapshots/fake/{snapshot_id}/abandon")

    assert r.status_code == 409
    assert "not running" in r.json()["detail"]


def test_abandon_unknown_snapshot_404(client: TestClient) -> None:
    assert client.post("/admin/v1/snapshots/fake/nope/abandon").status_code == 404
    assert client.post("/admin/v1/snapshots/nope/nope/abandon").status_code == 404


def test_process_conflicts_while_ingest_running(client: TestClient, factory) -> None:  # pyright: ignore[reportMissingParameterType]
    snapshot_id = client.post("/admin/v1/scrapers/fake/fetch").json()["snapshot_id"]
    # Seed an in-progress run that we never execute, so the source looks busy.
    with factory() as session:
        create_run(session, "fake", "https://fake.example")
    assert client.post(f"/admin/v1/snapshots/fake/{snapshot_id}/process").status_code == 409


def test_unknown_run_404(client: TestClient) -> None:
    assert client.get("/admin/v1/runs/999").status_code == 404


def test_gold_status_before_any_promote(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(build_routes, "DEFAULT_GOLD_DB_PATH", str(tmp_path / "gold.db"))
    data = client.get("/admin/v1/gold").json()
    assert data["exists"] is False
    assert data["status"] is None


def test_promote_builds_gold_and_reports_stats(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gold_path = tmp_path / "gold.db"
    monkeypatch.setattr(build_routes, "DEFAULT_GOLD_DB_PATH", str(gold_path))
    # seed silver through the API: fetch + process the fake source
    snapshot_id = client.post("/admin/v1/scrapers/fake/fetch").json()["snapshot_id"]
    client.post(f"/admin/v1/snapshots/fake/{snapshot_id}/process")

    assert client.post("/admin/v1/promote").status_code == 202
    data = client.get("/admin/v1/gold").json()  # background task already ran (TestClient)
    assert data["exists"] is True
    assert data["status"] == "completed"
    # the fake source produces no mentions, so nothing qualifies for gold
    assert data["stats"]["persons_kept"] == 0
    assert data["stats"]["persons_dropped"] == 2
    # concerts were derived (into silver) and copied; none here, but reported
    assert data["stats"]["concerts"] == 0


def test_promote_conflicts_while_running(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from composer_warehouse.build import BuildManifest, write_build_manifest

    gold_path = tmp_path / "gold.db"
    monkeypatch.setattr(build_routes, "DEFAULT_GOLD_DB_PATH", str(gold_path))
    write_build_manifest(gold_path, BuildManifest.start())
    assert client.post("/admin/v1/promote").status_code == 409


def test_promote_body_toggles_rules(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gold_path = tmp_path / "gold.db"
    monkeypatch.setattr(build_routes, "DEFAULT_GOLD_DB_PATH", str(gold_path))
    snapshot_id = client.post("/admin/v1/scrapers/fake/fetch").json()["snapshot_id"]
    client.post(f"/admin/v1/snapshots/fake/{snapshot_id}/process")

    # the fake persons have no performance evidence; with rule 1 off they're kept
    r = client.post("/admin/v1/promote", json={"drop_unevidenced_persons": False})
    assert r.status_code == 202
    data = client.get("/admin/v1/gold").json()
    assert data["status"] == "completed"
    assert data["stats"]["persons_kept"] == 2
    assert data["stats"]["persons_dropped"] == 0


def test_promote_body_resolves_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from composer_gold import PromoteConfig, PromoteStats

    calls: list[tuple[str, PromoteConfig]] = []

    def record_promote(session: object, gold_path: str, config: PromoteConfig) -> PromoteStats:
        calls.append((str(gold_path), config))
        return PromoteStats()

    monkeypatch.setattr(build_routes, "promote", record_promote)
    monkeypatch.setattr(build_routes, "DEFAULT_GOLD_DB_PATH", str(tmp_path / "gold.db"))

    # bodiless: configured defaults, all rules on
    assert client.post("/admin/v1/promote").status_code == 202
    # explicit values win over the defaults
    custom = tmp_path / "elsewhere.db"
    body = {"gold_path": str(custom), "collapse_duplicates": False}
    assert client.post("/admin/v1/promote", json=body).status_code == 202

    paths = [path for path, _ in calls]
    configs = [config for _, config in calls]
    assert paths == [str(tmp_path / "gold.db"), str(custom)]
    assert [c.collapse_duplicates for c in configs] == [True, False]
    assert all(c.drop_unevidenced_persons and c.prune_unreferenced for c in configs)


def test_promote_body_cannot_override_rule1_thresholds(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Rule 1's thresholds always come from the server's current
    rule1_config.json — the request body has no field for them."""
    from composer_gold import PersonRule1Config, PromoteConfig, PromoteStats, Rule1Config

    configs: list[PromoteConfig] = []

    def record_promote(session: object, gold_path: str, config: PromoteConfig) -> PromoteStats:
        configs.append(config)
        return PromoteStats()

    monkeypatch.setattr(build_routes, "promote", record_promote)
    monkeypatch.setattr(build_routes, "DEFAULT_GOLD_DB_PATH", str(tmp_path / "gold.db"))
    custom_rule1 = Rule1Config(persons=PersonRule1Config(min_concert_appearances=7))
    rule1_path = tmp_path / "rule1_config.json"
    custom_rule1.write_json(rule1_path)
    monkeypatch.setattr(build_routes, "DEFAULT_RULE1_CONFIG_PATH", str(rule1_path))

    # a body cannot smuggle rule-1 thresholds in; the server's file always wins
    assert client.post("/admin/v1/promote").status_code == 202
    assert client.post("/admin/v1/promote", json={"min_appearances": 2}).status_code == 202
    assert all(c.rule1 == custom_rule1 for c in configs)


def test_get_rule1_config_returns_current_file(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from composer_gold import EnsembleRule1Config, PersonRule1Config, Rule1Config

    rule1_path = tmp_path / "rule1_config.json"
    Rule1Config(
        persons=PersonRule1Config(min_concert_appearances=3, min_sitelinks=50),
        ensembles=EnsembleRule1Config(min_recording_appearances=2),
    ).write_json(rule1_path)
    monkeypatch.setattr(build_routes, "DEFAULT_RULE1_CONFIG_PATH", str(rule1_path))

    body = client.get("/admin/v1/rule1-config").json()

    assert body["persons"]["min_concert_appearances"] == 3
    assert body["persons"]["min_sitelinks"] == 50
    assert body["ensembles"]["min_recording_appearances"] == 2


def test_put_rule1_config_writes_file_and_is_read_back(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from composer_gold import Rule1Config

    rule1_path = tmp_path / "rule1_config.json"
    monkeypatch.setattr(build_routes, "DEFAULT_RULE1_CONFIG_PATH", str(rule1_path))

    body = {
        "persons": {
            "min_concert_appearances": 2,
            "min_recording_appearances": 1,
            "min_appearances_for_composers": 1,
            "min_sitelinks": None,
        },
        "ensembles": {"min_concert_appearances": 1, "min_recording_appearances": 4},
    }
    response = client.put("/admin/v1/rule1-config", json=body)
    assert response.status_code == 200
    assert response.json() == body

    # the file on disk now reflects the new thresholds
    on_disk = Rule1Config.from_json(rule1_path)
    assert on_disk.persons.min_appearances_for_composers == 1
    assert on_disk.ensembles.min_recording_appearances == 4
    # and a later GET reads the same values back, with no server restart needed
    assert client.get("/admin/v1/rule1-config").json() == body


def test_put_rule1_config_rejects_negative_thresholds(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(build_routes, "DEFAULT_RULE1_CONFIG_PATH", str(tmp_path / "rule1_config.json"))
    body = {
        "persons": {
            "min_concert_appearances": -1,
            "min_recording_appearances": 1,
            "min_appearances_for_composers": 0,
            "min_sitelinks": None,
        },
        "ensembles": {"min_concert_appearances": 1, "min_recording_appearances": 1},
    }
    assert client.put("/admin/v1/rule1-config", json=body).status_code == 422
    # the invalid body was never written
    assert not (tmp_path / "rule1_config.json").exists()


def test_promote_body_resolves_min_referrers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from composer_gold import PromoteConfig, PromoteStats

    configs: list[PromoteConfig] = []

    def record_promote(session: object, gold_path: str, config: PromoteConfig) -> PromoteStats:
        configs.append(config)
        return PromoteStats()

    monkeypatch.setattr(build_routes, "promote", record_promote)
    monkeypatch.setattr(build_routes, "DEFAULT_GOLD_DB_PATH", str(tmp_path / "gold.db"))
    monkeypatch.setattr(build_routes, "DEFAULT_MIN_REFERRERS", 3)

    # omitted: the configured server default; explicit value wins over it
    assert client.post("/admin/v1/promote").status_code == 202
    assert client.post("/admin/v1/promote", json={"min_referrers": 2}).status_code == 202
    assert [c.min_referrers for c in configs] == [3, 2]


def test_promote_rejects_invalid_body(client: TestClient) -> None:
    assert client.post("/admin/v1/promote", json={"min_referrers": 0}).status_code == 422
    assert client.post("/admin/v1/promote", json={"min_referrers": "abc"}).status_code == 422


def test_silver_status_before_any_rebuild(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from composer_config import settings

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path}/silver.db")
    data = client.get("/admin/v1/silver").json()
    assert data["exists"] is False
    assert data["status"] is None


def test_rebuild_silver_replays_bucket_and_reports_stats(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from composer_config import settings

    silver_path = tmp_path / "silver.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{silver_path}")
    snapshot_id = client.post("/admin/v1/scrapers/fake/fetch").json()["snapshot_id"]
    assert snapshot_id

    assert client.post("/admin/v1/rebuild-silver").status_code == 202
    data = client.get("/admin/v1/silver").json()  # background task already ran (TestClient)
    assert data["exists"] is True
    assert data["status"] == "completed"
    assert data["stats"]["sources_replayed"] == 1
    assert data["stats"]["records_seen"] == 2


def test_rebuild_silver_conflicts_while_running(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from composer_config import settings
    from composer_warehouse.build import BuildManifest, write_build_manifest

    silver_path = tmp_path / "silver.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{silver_path}")
    write_build_manifest(silver_path, BuildManifest.start())
    assert client.post("/admin/v1/rebuild-silver").status_code == 409


def test_rebuild_silver_rejects_non_sqlite_database(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from composer_config import settings

    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://user:pass@host:5432/composers")
    assert client.get("/admin/v1/silver").json()["exists"] is False
    r = client.post("/admin/v1/rebuild-silver")
    assert r.status_code == 400
    assert "sqlite" in r.json()["detail"]


def test_admin_key_guard(client: TestClient) -> None:
    # A bare client sends no X-Admin-Key header; the fixture client sends the right one.
    assert TestClient(admin_app).get("/admin/v1/scrapers").status_code == 401
    assert client.get("/admin/v1/scrapers", headers={"X-Admin-Key": "wrong"}).status_code == 401
    assert client.get("/admin/v1/scrapers").status_code == 200


def test_admin_key_unset_fails_closed(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    from composer_config import settings

    monkeypatch.setattr(settings, "admin_api_key", None)
    r = client.get("/admin/v1/scrapers")
    assert r.status_code == 503
    assert "ADMIN_API_KEY" in r.json()["detail"]
