"""The stubbed admin-API client the crawls-dashboard tests drive their views with.

Shared by the view tests and the pipeline-button tests so both see one stub and
one set of payloads; no test in this package touches the network.
"""

from __future__ import annotations

from typing import Any

import pytest
import scrapers.crawl_views as crawl_views
from scrapers.api import AdminAPIError

SNAPSHOT_PAYLOAD = {
    "source": "archive",
    "id": "2026-07-02T09:52:31-e8533a60",
    "status": "completed",
    "started_at": "2026-07-02T09:52:31+00:00",
    "finished_at": "2026-07-02T10:00:40+00:00",
    "record_count": 42,
    "size_bytes": 1024,
    "error": None,
}

CRAWL_PAYLOAD = {
    "name": "archive",
    "seeds": ["https://example.org/archive"],
    "use_sitemap": True,
    "use_common_crawl": False,
    "allow_patterns": ["*example.org/archive*"],
    "relevance_query": "composer biography",
    "score_threshold": 0.0,
    "follow_links": True,
    "max_depth": 2,
    "max_pages": None,
    "excluded_selector": None,
    "request_delay_s": 0.5,
    "respect_robots": True,
    "editable": True,
    "last_snapshot": SNAPSHOT_PAYLOAD,
}

CODE_CRAWL_PAYLOAD = {
    **CRAWL_PAYLOAD,
    "name": "code-crawl",
    "editable": False,
    "last_snapshot": None,
}


class StubAPI:
    def __init__(self, crawls: list[dict[str, Any]] | None = None, error: str | None = None) -> None:
        self._crawls = crawls or []
        self._error = error
        self.saved: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[str] = []
        self.extracted: list[str] = []
        self.loaded: list[str] = []
        self.piped: list[str] = []

    def _maybe_fail(self) -> None:
        if self._error:
            raise AdminAPIError(self._error)

    def list_crawls(self) -> list[dict[str, Any]]:
        self._maybe_fail()
        return self._crawls

    def get_crawl(self, name: str) -> dict[str, Any]:
        self._maybe_fail()
        for crawl in self._crawls:
            if crawl["name"] == name:
                return crawl
        raise AdminAPIError(f"API returned 404: unknown crawl {name!r}")

    def put_crawl(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._maybe_fail()
        self.saved.append((name, payload))
        return {**CRAWL_PAYLOAD, "name": name}

    def delete_crawl(self, name: str) -> None:
        self._maybe_fail()
        self.deleted.append(name)

    def start_crawl(self, name: str) -> dict[str, Any]:
        self._maybe_fail()
        return {"source": name, "snapshot_id": "snap-1", "status": "running"}

    def start_extract(self, name: str) -> dict[str, Any]:
        self._maybe_fail()
        self.extracted.append(name)
        return {"source": name, "snapshot_id": "snap-2", "status": "running"}

    def load_crawl(self, name: str) -> dict[str, Any]:
        self._maybe_fail()
        self.loaded.append(name)
        return {"source": name, "run_id": 7, "status": "running"}

    def run_crawl_pipeline(self, name: str) -> dict[str, Any]:
        self._maybe_fail()
        self.piped.append(name)
        return {"source": name, "snapshot_id": "snap-3", "status": "running"}


def install(monkeypatch: pytest.MonkeyPatch, stub: StubAPI) -> None:
    fake = type("FakeAdminAPI", (), {"from_env": classmethod(lambda cls: stub)})
    monkeypatch.setattr(crawl_views, "AdminAPI", fake)
