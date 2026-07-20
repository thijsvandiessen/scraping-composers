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

import composer_admin.deps as admin_deps
import composer_admin.routes as admin_routes
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
    monkeypatch.setattr(admin_routes, "DEFAULT_BUCKET_PATH", str(bucket_path))
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
    assert snapshots[1]["status"] == "unknown"
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


def test_process_conflicts_while_ingest_running(client: TestClient, factory) -> None:  # pyright: ignore[reportMissingParameterType]
    snapshot_id = client.post("/admin/v1/scrapers/fake/fetch").json()["snapshot_id"]
    # Seed an in-progress run that we never execute, so the source looks busy.
    with factory() as session:
        create_run(session, _FakeSource())
    assert client.post(f"/admin/v1/snapshots/fake/{snapshot_id}/process").status_code == 409


def test_unknown_run_404(client: TestClient) -> None:
    assert client.get("/admin/v1/runs/999").status_code == 404


def test_gold_status_before_any_promote(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(admin_routes, "DEFAULT_GOLD_DB_PATH", str(tmp_path / "gold.db"))
    data = client.get("/admin/v1/gold").json()
    assert data["exists"] is False
    assert data["status"] is None


def test_promote_builds_gold_and_reports_stats(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gold_path = tmp_path / "gold.db"
    monkeypatch.setattr(admin_routes, "DEFAULT_GOLD_DB_PATH", str(gold_path))
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
    monkeypatch.setattr(admin_routes, "DEFAULT_GOLD_DB_PATH", str(gold_path))
    write_build_manifest(gold_path, BuildManifest.start())
    assert client.post("/admin/v1/promote").status_code == 409


def test_promote_body_toggles_rules(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gold_path = tmp_path / "gold.db"
    monkeypatch.setattr(admin_routes, "DEFAULT_GOLD_DB_PATH", str(gold_path))
    snapshot_id = client.post("/admin/v1/scrapers/fake/fetch").json()["snapshot_id"]
    client.post(f"/admin/v1/snapshots/fake/{snapshot_id}/process")

    # the fake persons have no performance evidence; with rule 1 off they're kept
    r = client.post("/admin/v1/promote", json={"drop_unevidenced_persons": False})
    assert r.status_code == 202
    data = client.get("/admin/v1/gold").json()
    assert data["status"] == "completed"
    assert data["stats"]["persons_kept"] == 2
    assert data["stats"]["persons_dropped"] == 0


def test_promote_body_resolves_path_and_sitelinks(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from composer_gold import PromoteConfig, PromoteStats

    calls: list[tuple[str, PromoteConfig]] = []

    def record_promote(session: object, gold_path: str, config: PromoteConfig) -> PromoteStats:
        calls.append((str(gold_path), config))
        return PromoteStats()

    monkeypatch.setattr(admin_routes, "promote", record_promote)
    monkeypatch.setattr(admin_routes, "DEFAULT_GOLD_DB_PATH", str(tmp_path / "gold.db"))
    monkeypatch.setattr(admin_routes, "DEFAULT_MIN_SITELINKS", 50)

    # bodiless: configured defaults, all rules on
    assert client.post("/admin/v1/promote").status_code == 202
    # explicit null: the sitelink signal is switched off, not defaulted
    assert client.post("/admin/v1/promote", json={"min_sitelinks": None}).status_code == 202
    # explicit values win over the defaults
    custom = tmp_path / "elsewhere.db"
    body = {"min_sitelinks": 120, "gold_path": str(custom), "collapse_duplicates": False}
    assert client.post("/admin/v1/promote", json=body).status_code == 202

    paths = [path for path, _ in calls]
    configs = [config for _, config in calls]
    assert paths == [str(tmp_path / "gold.db"), str(tmp_path / "gold.db"), str(custom)]
    assert [c.min_sitelinks for c in configs] == [50, None, 120]
    assert [c.collapse_duplicates for c in configs] == [True, True, False]
    assert all(c.drop_unevidenced_persons and c.prune_unreferenced for c in configs)


def test_promote_rejects_invalid_body(client: TestClient) -> None:
    assert client.post("/admin/v1/promote", json={"min_sitelinks": "abc"}).status_code == 422
    assert client.post("/admin/v1/promote", json={"min_sitelinks": -1}).status_code == 422


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
