"""AdminAPI crawl/extract client methods against a mock transport — no network."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from scrapers.api import AdminAPI, AdminAPIError

# Only the fields these tests assert on; the view tests exercise the full shape.
CRAWL_PAYLOAD = {"name": "archive", "seeds": ["https://example.org/archive"]}


def _api(handler: Any) -> AdminAPI:
    return AdminAPI(base_url="http://testserver", transport=httpx.MockTransport(handler))


def test_client_crawl_methods_hit_expected_endpoints() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if (request.method, request.url.path) == ("GET", "/admin/v1/crawls"):
            return httpx.Response(200, json=[CRAWL_PAYLOAD])
        if (request.method, request.url.path) == ("GET", "/admin/v1/crawls/archive"):
            return httpx.Response(200, json=CRAWL_PAYLOAD)
        if (request.method, request.url.path) == ("PUT", "/admin/v1/crawls/archive"):
            assert json.loads(request.content)["seeds"] == ["https://example.org/archive"]
            return httpx.Response(200, json=CRAWL_PAYLOAD)
        if (request.method, request.url.path) == ("POST", "/admin/v1/crawls/archive/extract"):
            return httpx.Response(
                202, json={"source": "archive", "snapshot_id": "snap-2", "status": "running"}
            )
        if (request.method, request.url.path) == ("POST", "/admin/v1/crawls/archive/process"):
            return httpx.Response(202, json={"run_id": 7, "source": "archive", "status": "running"})
        assert (request.method, request.url.path) == ("POST", "/admin/v1/crawls/archive/fetch")
        return httpx.Response(202, json={"source": "archive", "snapshot_id": "snap-1", "status": "running"})

    api = _api(handler)
    assert api.list_crawls() == [CRAWL_PAYLOAD]
    assert api.get_crawl("archive") == CRAWL_PAYLOAD
    assert api.put_crawl("archive", {"seeds": ["https://example.org/archive"]}) == CRAWL_PAYLOAD
    assert api.start_crawl("archive")["snapshot_id"] == "snap-1"
    assert api.start_extract("archive")["snapshot_id"] == "snap-2"
    assert api.load_crawl("archive")["run_id"] == 7


def test_client_delete_crawl_handles_204() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("DELETE", "/admin/v1/crawls/archive")
        return httpx.Response(204)

    assert _api(handler).delete_crawl("archive") is None


def test_client_delete_crawl_surfaces_conflict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "crawl 'x' is code-registered"})

    with pytest.raises(AdminAPIError, match="code-registered"):
        _api(handler).delete_crawl("x")
