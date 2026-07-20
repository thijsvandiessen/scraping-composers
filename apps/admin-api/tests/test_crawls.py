"""Crawl-config endpoint tests: a tmp_path store and bucket, no network.

Like the scraper tests, the Starlette TestClient runs background tasks
synchronously, so a started crawl has already finished when the POST returns.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import composer_admin.crawl_routes as crawl_routes
import composer_admin.routes as admin_routes
import httpx
import pytest
from composer_admin import admin_app
from composer_bronze.bucket import LocalBucket, SnapshotManifest
from composer_crawler import CrawlConfig, Crawler
from fastapi.testclient import TestClient

CODE_CONFIG = CrawlConfig(name="code-crawl", seeds=("https://code.example/",))

PAYLOAD = {
    "seeds": ["https://example.org/archive"],
    "follow_links": True,
    "allow_patterns": [r"example\.org/archive"],
    "max_depth": 1,
    "pagination": {"type": "page_param", "param": "p", "start": 1},
}


@pytest.fixture
def bucket_path(tmp_path: Path) -> Path:
    return tmp_path / "bucket"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bucket_path: Path) -> Iterator[TestClient]:
    monkeypatch.setattr(admin_routes, "DEFAULT_BUCKET_PATH", str(bucket_path))
    monkeypatch.setattr(crawl_routes, "DEFAULT_CRAWL_CONFIGS_PATH", str(tmp_path / "crawl_configs.json"))
    monkeypatch.setattr(crawl_routes, "CRAWL_REGISTRY", {"code-crawl": CODE_CONFIG})
    monkeypatch.setattr(crawl_routes, "REGISTRY", {"imslp": object()})
    from composer_config import settings

    monkeypatch.setattr(settings, "admin_api_key", "test-key")
    yield TestClient(admin_app, headers={"X-Admin-Key": "test-key"})


def test_crud_round_trip(client: TestClient) -> None:
    r = client.put("/admin/v1/crawls/archive", json=PAYLOAD)
    assert r.status_code == 200
    created = r.json()
    assert created["name"] == "archive"
    assert created["editable"] is True
    assert created["pagination"] == {"type": "page_param", "param": "p", "start": 1}
    assert created["last_snapshot"] is None

    assert client.get("/admin/v1/crawls/archive").json() == created

    update = {**PAYLOAD, "seeds": ["https://example.org/new"], "pagination": None}
    updated = client.put("/admin/v1/crawls/archive", json=update).json()
    assert updated["seeds"] == ["https://example.org/new"]
    assert updated["pagination"] is None

    assert client.delete("/admin/v1/crawls/archive").status_code == 204
    assert client.get("/admin/v1/crawls/archive").status_code == 404


def test_list_merges_code_and_stored(client: TestClient) -> None:
    client.put("/admin/v1/crawls/archive", json=PAYLOAD)
    by_name = {c["name"]: c for c in client.get("/admin/v1/crawls").json()}
    assert set(by_name) == {"archive", "code-crawl"}
    assert by_name["archive"]["editable"] is True
    assert by_name["code-crawl"]["editable"] is False


def test_code_registered_is_read_only(client: TestClient) -> None:
    assert client.put("/admin/v1/crawls/code-crawl", json=PAYLOAD).status_code == 409
    assert client.delete("/admin/v1/crawls/code-crawl").status_code == 409
    # but it is visible and crawlable
    assert client.get("/admin/v1/crawls/code-crawl").status_code == 200


def test_put_rejects_scraper_name_collision(client: TestClient) -> None:
    r = client.put("/admin/v1/crawls/imslp", json=PAYLOAD)
    assert r.status_code == 409
    assert "scraper" in r.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {"seeds": []},  # empty seeds (pydantic min_length)
        {"seeds": ["https://x.example/"], "follow_links": True},  # no allow patterns
        {"seeds": ["https://x.example/"], "follow_links": True, "allow_patterns": ["("]},  # bad regex
        {"seeds": ["https://x.example/"], "pagination": {"type": "cursor"}},  # unknown pagination
    ],
)
def test_put_rejects_invalid_config(client: TestClient, body: dict[str, object]) -> None:
    assert client.put("/admin/v1/crawls/bad", json=body).status_code == 422


def test_put_rejects_traversal_name(client: TestClient) -> None:
    # A literal ".." is normalized away before routing; the encoded form
    # reaches the handler and must be rejected by the name validation.
    r = client.put("/admin/v1/crawls/%2e%2e", json=PAYLOAD)
    assert r.status_code == 422
    assert "path segment" in r.json()["detail"]


def test_unknown_crawl_404(client: TestClient) -> None:
    assert client.get("/admin/v1/crawls/nope").status_code == 404
    assert client.delete("/admin/v1/crawls/nope").status_code == 404
    assert client.post("/admin/v1/crawls/nope/fetch").status_code == 404


def test_fetch_writes_snapshot_and_manifest(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, bucket_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html="<html><body>hello</body></html>")

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        crawl_routes, "_crawler", lambda config: Crawler(config, client=httpx.Client(transport=transport))
    )
    client.put("/admin/v1/crawls/archive", json={"seeds": ["https://example.org/"], "respect_robots": False})

    r = client.post("/admin/v1/crawls/archive/fetch")
    assert r.status_code == 202
    snapshot_id = r.json()["snapshot_id"]

    ndjson = bucket_path / "archive" / snapshot_id / "records.ndjson"
    assert len(ndjson.read_text().strip().splitlines()) == 1
    manifest = json.loads((ndjson.parent / "manifest.json").read_text())
    assert manifest["status"] == "completed"
    assert manifest["record_count"] == 1

    last = client.get("/admin/v1/crawls/archive").json()["last_snapshot"]
    assert last["id"] == snapshot_id
    assert last["status"] == "completed"


def test_fetch_conflicts_while_crawl_running(client: TestClient, bucket_path: Path) -> None:
    client.put("/admin/v1/crawls/archive", json=PAYLOAD)
    LocalBucket(bucket_path).write_manifest(SnapshotManifest.start("archive", "2026-01-01T00:00:00-abc"))
    assert client.post("/admin/v1/crawls/archive/fetch").status_code == 409


def test_failed_crawl_records_failed_manifest(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, bucket_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("connection torn down")

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        crawl_routes, "_crawler", lambda config: Crawler(config, client=httpx.Client(transport=transport))
    )
    client.put("/admin/v1/crawls/archive", json={"seeds": ["https://example.org/"], "respect_robots": False})

    snapshot_id = client.post("/admin/v1/crawls/archive/fetch").json()["snapshot_id"]
    manifest = json.loads((bucket_path / "archive" / snapshot_id / "manifest.json").read_text())
    assert manifest["status"] == "failed"


def test_admin_key_guard(client: TestClient) -> None:
    assert TestClient(admin_app).get("/admin/v1/crawls").status_code == 401
    assert client.get("/admin/v1/crawls").status_code == 200
