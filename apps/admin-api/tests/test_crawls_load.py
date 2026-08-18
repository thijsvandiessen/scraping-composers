"""Loading crawl-source snapshots into the DB: the Load page's snapshot list and
the per-crawl process endpoint.

A crawl source's bucket dir mixes raw ``pages`` snapshots (``_type: "crawl"``)
with LLM-extracted ``documents`` snapshots; only the latter load. These tests
seed the bucket directly — no crawl/extract run needed — and drive the loading
paths. The TestClient runs background tasks synchronously, so a started load has
already finished when the POST returns.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import composer_admin.crawl_routes as crawl_routes
import composer_admin.deps as admin_deps
import composer_admin.routes as admin_routes
import composer_admin.snapshots as admin_snapshots
import pytest
from composer_admin import admin_app
from composer_bronze.bucket import LocalBucket, SnapshotManifest
from composer_bronze.scraper import write_documents
from composer_models.db import init_db
from composer_schema import EntityDocument
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

PAYLOAD = {"seeds": ["https://example.org/archive"]}


@pytest.fixture
def bucket_path(tmp_path: Path) -> Path:
    return tmp_path / "bucket"


@pytest.fixture
def factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    return init_db(engine)


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    bucket_path: Path,
    factory: sessionmaker[Session],
) -> Iterator[TestClient]:
    monkeypatch.setattr(admin_deps, "_session_factory", factory)
    monkeypatch.setattr(admin_snapshots, "DEFAULT_BUCKET_PATH", str(bucket_path))
    monkeypatch.setattr(admin_routes, "REGISTRY", {})
    monkeypatch.setattr(admin_snapshots, "REGISTRY", {})
    monkeypatch.setattr(crawl_routes, "DEFAULT_CRAWL_CONFIGS_PATH", str(tmp_path / "crawl_configs.json"))
    monkeypatch.setattr(crawl_routes, "CRAWL_REGISTRY", {})
    monkeypatch.setattr(crawl_routes, "REGISTRY", {})
    from composer_config import settings

    monkeypatch.setattr(settings, "admin_api_key", "test-key")
    yield TestClient(admin_app, headers={"X-Admin-Key": "test-key"})


def _entity(name: str) -> EntityDocument:
    return EntityDocument(
        id=f"person:{name}",
        url=None,
        source_name="archive",
        ingested_at=datetime(2026, 1, 1, tzinfo=UTC),
        name=name,
        raw={"id": name},
    )


def _seed_documents(bucket_path: Path, run_id: str = "2026-01-01T00:00:00-docs") -> str:
    """Write a loadable ``documents`` snapshot, as the LLM ``extract`` step would."""
    write_documents(LocalBucket(bucket_path), "archive", iter([_entity("Bach"), _entity("Adams")]), run_id)
    return run_id


def _seed_pages(bucket_path: Path, run_id: str = "2026-02-01T00:00:00-pages") -> str:
    """Write a raw ``pages`` crawl snapshot (not loadable)."""
    bucket = LocalBucket(bucket_path)
    bucket.write_records("archive", run_id, [{"_type": "crawl", "url": "https://example.org/"}])
    bucket.write_manifest(SnapshotManifest.start("archive", run_id).completed(1))
    return run_id


def test_snapshots_are_kinded_and_documents_load(client: TestClient, bucket_path: Path) -> None:
    pages_id = _seed_pages(bucket_path)
    docs_id = _seed_documents(bucket_path)

    snaps = {s["id"]: s for s in client.get("/admin/v1/snapshots").json() if s["source"] == "archive"}
    assert snaps[pages_id]["kind"] == "pages"  # raw crawl — not loadable
    assert snaps[docs_id]["kind"] == "documents"  # extracted docs — loadable

    r = client.post(f"/admin/v1/snapshots/archive/{docs_id}/process")
    assert r.status_code == 202
    run = client.get(f"/admin/v1/runs/{r.json()['run_id']}").json()
    assert run["status"] == "completed"
    assert run["records_seen"] == 2


def test_process_pages_snapshot_conflicts(client: TestClient, bucket_path: Path) -> None:
    pages_id = _seed_pages(bucket_path)
    r = client.post(f"/admin/v1/snapshots/archive/{pages_id}/process")
    assert r.status_code == 409
    assert "crawled pages" in r.json()["detail"]


def test_process_crawl_loads_latest_documents(client: TestClient, bucket_path: Path) -> None:
    client.put("/admin/v1/crawls/archive", json=PAYLOAD)
    _seed_documents(bucket_path)  # the extract to load
    _seed_pages(bucket_path)  # a later, non-loadable crawl — must be skipped

    r = client.post("/admin/v1/crawls/archive/process")
    assert r.status_code == 202
    assert client.get(f"/admin/v1/runs/{r.json()['run_id']}").json()["status"] == "completed"


def test_process_crawl_without_documents_conflicts(client: TestClient, bucket_path: Path) -> None:
    client.put("/admin/v1/crawls/archive", json=PAYLOAD)
    _seed_pages(bucket_path)  # pages only, never extracted
    r = client.post("/admin/v1/crawls/archive/process")
    assert r.status_code == 409
    assert "no extracted snapshot" in r.json()["detail"]


def test_process_crawl_unknown_404(client: TestClient) -> None:
    assert client.post("/admin/v1/crawls/nope/process").status_code == 404
