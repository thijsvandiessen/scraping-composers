"""Admin API tests using an in-memory database.

The Starlette TestClient runs FastAPI BackgroundTasks synchronously once the
response is returned, so a triggered run has already finished by the time the
``POST .../run`` call returns here — which lets us assert the completed run.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import composer_ingest.admin.deps as admin_deps
import composer_ingest.admin.routes as admin_routes
from composer_ingest.admin import admin_app
from composer_ingest.etl.db import init_db
from composer_ingest.etl.ingestion import create_run
from composer_ingest.scraper.sources import (
    EntityDocument,
    RefreshCadence,
    SourceAdapter,
)

_INGESTED_AT_RAW = {"id": "x"}


def _person(name: str) -> EntityDocument:
    from datetime import UTC, datetime

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


@pytest.fixture
def factory():  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    return init_db(engine)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, factory) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(admin_deps, "_session_factory", factory)
    registry = {"fake": _FakeSource(), "archive": _ArchiveSource()}
    monkeypatch.setattr(admin_routes, "REGISTRY", registry)
    yield TestClient(admin_app)


def test_list_scrapers_reports_cadence_and_due(client: TestClient) -> None:
    r = client.get("/admin/v1/scrapers")
    assert r.status_code == 200
    by_name = {s["name"]: s for s in r.json()}
    assert by_name["fake"]["cadence"] == "monthly"
    assert by_name["fake"]["due"] is True  # never run, monthly cadence
    assert by_name["fake"]["last_run"] is None
    assert by_name["archive"]["cadence"] == "static"
    assert by_name["archive"]["due"] is False  # static sources are never auto-due


def test_run_scraper_executes_in_background(client: TestClient) -> None:
    r = client.post("/admin/v1/scrapers/fake/run")
    assert r.status_code == 202
    started = r.json()
    assert started["source"] == "fake"
    run_id = started["run_id"]

    detail = client.get(f"/admin/v1/runs/{run_id}")
    assert detail.status_code == 200
    data = detail.json()
    assert data["status"] == "completed"
    assert data["records_seen"] == 2
    assert data["records_new"] == 2


def test_run_scraper_marks_source_no_longer_due(client: TestClient) -> None:
    client.post("/admin/v1/scrapers/fake/run")
    fake = next(s for s in client.get("/admin/v1/scrapers").json() if s["name"] == "fake")
    assert fake["due"] is False  # just ran, within the monthly window
    assert fake["last_run"]["status"] == "completed"


def test_run_unknown_scraper_404(client: TestClient) -> None:
    assert client.post("/admin/v1/scrapers/nope/run").status_code == 404


def test_get_unknown_scraper_404(client: TestClient) -> None:
    assert client.get("/admin/v1/scrapers/nope").status_code == 404


def test_run_conflicts_while_already_running(client: TestClient, factory) -> None:  # type: ignore[no-untyped-def]
    # Seed an in-progress run that we never execute, so the source looks busy.
    with factory() as session:
        create_run(session, _FakeSource())
    r = client.post("/admin/v1/scrapers/fake/run")
    assert r.status_code == 409


def test_run_due_starts_only_due_scrapers(client: TestClient) -> None:
    r = client.post("/admin/v1/scrapers/run-due")
    assert r.status_code == 200
    started = {s["source"] for s in r.json()}
    assert "fake" in started  # monthly, never run -> due
    assert "archive" not in started  # static -> never auto-due


def test_unknown_run_404(client: TestClient) -> None:
    assert client.get("/admin/v1/runs/999").status_code == 404


def test_admin_key_guard(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.setenv("ADMIN_API_KEY", "secret")
    assert client.get("/admin/v1/scrapers").status_code == 401
    ok = client.get("/admin/v1/scrapers", headers={"X-Admin-Key": "secret"})
    assert ok.status_code == 200
