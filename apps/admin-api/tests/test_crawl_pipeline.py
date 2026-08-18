"""``POST /crawls/{name}/run``: the three stages chained behind one trigger.

The stages themselves are unchanged and still tested separately; what matters
here is that they run back to back, that each still records its own snapshot or
run, and that the chain stops at the first failure instead of loading whatever a
broken stage left behind. The TestClient runs background tasks synchronously, so
the whole chain has finished by the time the POST returns.
"""

from collections.abc import Iterator
from pathlib import Path

import composer_admin.crawl_routes as crawl_routes
import composer_admin.deps as admin_deps
import composer_admin.routes as admin_routes
import composer_admin.snapshots as admin_snapshots
import composer_crawler.crawler as crawler_mod
import pytest
from composer_admin import admin_app
from composer_bronze.bucket import LocalBucket, SnapshotManifest
from composer_crawler import Crawler
from composer_crawler.testing import (
    FakeMarkdown,
    FakeResult,
    FakeWebCrawler,
    stub_discover,
    web_crawler_factory,
)
from composer_extract import ExtractedConcert, ExtractedWork, PageExtraction
from composer_models.db import init_db
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

URL = "https://example.org/"
PAYLOAD = {"seeds": [URL], "respect_robots": False}


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
    # The fake extractors below don't implement the .model/.request_options() the
    # ledger fingerprints, and none of these tests are about the ledger itself —
    # same reasoning as replacing _extractor wholesale rather than its cache.
    monkeypatch.setattr(settings, "extract_ledger_enabled", False)
    monkeypatch.setattr(settings, "extract_cache_path", str(tmp_path / "extract-cache.db"))
    yield TestClient(admin_app, headers={"X-Admin-Key": "test-key"})


class FakeExtractor:
    """Stands in for the Ollama model: one concert, regardless of the markdown."""

    def extract_page(self, markdown: str, metadata: dict[str, str]) -> PageExtraction:
        return PageExtraction(
            concerts=[
                ExtractedConcert(
                    date="2026-03-01",
                    conductors=["Tarmo Peltokoski"],
                    works=[ExtractedWork(title="Symphony No 2", composer="Sibelius")],
                )
            ]
        )


def _stub_crawler(monkeypatch: pytest.MonkeyPatch, markdown: str = "# Sibelius") -> None:
    monkeypatch.setattr(crawler_mod, "discover_urls", stub_discover([URL]))
    fake = FakeWebCrawler(
        {URL: FakeResult(URL, html="<p>x</p>", markdown=FakeMarkdown(fit_markdown=markdown))}
    )
    monkeypatch.setattr(
        crawl_routes,
        "_crawler",
        lambda config: Crawler(config, web_crawler_factory=web_crawler_factory(fake)),
    )


def _kinds(bucket_path: Path) -> dict[str, str]:
    """Each of the crawl's snapshots by id, mapped to its kind."""
    return {s.manifest.run_id: s.kind for s in LocalBucket(bucket_path).list_snapshots("archive")}


def test_run_chains_crawl_extract_and_load(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, bucket_path: Path
) -> None:
    _stub_crawler(monkeypatch)
    monkeypatch.setattr(crawl_routes, "_extractor", FakeExtractor)
    client.put("/admin/v1/crawls/archive", json=PAYLOAD)

    r = client.post("/admin/v1/crawls/archive/run")
    assert r.status_code == 202
    crawl_snapshot = r.json()["snapshot_id"]

    kinds = _kinds(bucket_path)
    assert kinds[crawl_snapshot] == "pages", "the id returned up front is the crawl's"
    assert sorted(kinds.values()) == ["documents", "pages"], "each stage kept its own snapshot"

    (run,) = [r for r in client.get("/admin/v1/runs").json() if r["source"] == "archive"]
    assert run["status"] == "completed"
    assert run["records_seen"] == 3  # two person entities plus the work mention


def test_run_stops_before_loading_when_the_crawl_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, bucket_path: Path
) -> None:
    monkeypatch.setattr(crawler_mod, "discover_urls", stub_discover([URL]))

    def boom(config: object) -> Crawler:
        raise RuntimeError("network is down")

    monkeypatch.setattr(crawl_routes, "_crawler", boom)
    client.put("/admin/v1/crawls/archive", json=PAYLOAD)

    assert client.post("/admin/v1/crawls/archive/run").status_code == 202

    assert "documents" not in _kinds(bucket_path).values(), "nothing was extracted"
    assert client.get("/admin/v1/runs").json() == [], "and nothing was loaded"


def test_run_stops_before_loading_when_the_model_is_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, bucket_path: Path
) -> None:
    """A dead model fails the extract stage outright — unlike a page the model
    merely mangles, which ``extract_documents`` skips on its own."""
    _stub_crawler(monkeypatch)

    class DeadOllama:
        def extract_page(self, markdown: str, metadata: dict[str, str]) -> PageExtraction:
            raise ConnectionError("ollama is not running")

    monkeypatch.setattr(crawl_routes, "_extractor", DeadOllama)
    client.put("/admin/v1/crawls/archive", json=PAYLOAD)

    assert client.post("/admin/v1/crawls/archive/run").status_code == 202

    assert client.get("/admin/v1/runs").json() == [], "a failed extract must not be loaded"


def test_run_unknown_crawl_404(client: TestClient) -> None:
    assert client.post("/admin/v1/crawls/nope/run").status_code == 404


def test_run_conflicts_while_a_snapshot_is_in_flight(client: TestClient, bucket_path: Path) -> None:
    client.put("/admin/v1/crawls/archive", json=PAYLOAD)
    LocalBucket(bucket_path).write_manifest(SnapshotManifest.start("archive", "2026-01-01T00:00:00-abc"))

    r = client.post("/admin/v1/crawls/archive/run")
    assert r.status_code == 409
    assert "already in progress" in r.json()["detail"]
