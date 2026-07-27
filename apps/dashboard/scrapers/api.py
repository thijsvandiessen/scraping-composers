"""HTTP clients for the two FastAPI apps — the dashboard's only I/O.

The dashboard deliberately has no database connection; scraping and loading go
through the admin API (``/admin/v1``), data inspection through the read-only
consumer API (``/v1``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

TIMEOUT = 10.0


class AdminAPIError(Exception):
    """A failed API call, with a message fit to show on the page."""


@dataclass
class _BaseAPI:
    base_url: str
    api_key: str | None = None
    transport: httpx.BaseTransport | None = None

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"X-Admin-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(
                base_url=self.base_url, headers=headers, timeout=TIMEOUT, transport=self.transport
            ) as client:
                response = client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise AdminAPIError(f"API unreachable at {self.base_url}: {exc}") from exc
        if response.is_error:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise AdminAPIError(f"API returned {response.status_code}: {detail}")
        if response.status_code == 204:
            return None
        return response.json()


@dataclass
class AdminAPI(_BaseAPI):
    """Client for the admin API: manage scrapers, snapshots, and loads."""

    @classmethod
    def from_env(cls) -> AdminAPI:
        from django.conf import settings

        return cls(base_url=settings.ADMIN_API_URL, api_key=settings.ADMIN_API_KEY)

    def list_scrapers(self) -> list[dict[str, Any]]:
        scrapers: list[dict[str, Any]] = self._request("GET", "/admin/v1/scrapers")
        return scrapers

    def fetch_scraper(self, name: str) -> dict[str, Any]:
        started: dict[str, Any] = self._request("POST", f"/admin/v1/scrapers/{name}/fetch")
        return started

    def fetch_due(self) -> list[dict[str, Any]]:
        started: list[dict[str, Any]] = self._request("POST", "/admin/v1/scrapers/fetch-due")
        return started

    def list_snapshots(self) -> list[dict[str, Any]]:
        snapshots: list[dict[str, Any]] = self._request("GET", "/admin/v1/snapshots")
        return snapshots

    def process_snapshot(self, source: str, snapshot_id: str) -> dict[str, Any]:
        started: dict[str, Any] = self._request("POST", f"/admin/v1/snapshots/{source}/{snapshot_id}/process")
        return started

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = self._request("GET", "/admin/v1/runs", params={"limit": limit})
        return runs

    def gold_status(self) -> dict[str, Any]:
        status: dict[str, Any] = self._request("GET", "/admin/v1/gold")
        return status

    def start_promote(self, options: dict[str, Any] | None = None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"json": options} if options else {}
        status: dict[str, Any] = self._request("POST", "/admin/v1/promote", **kwargs)
        return status

    def list_crawls(self) -> list[dict[str, Any]]:
        crawls: list[dict[str, Any]] = self._request("GET", "/admin/v1/crawls")
        return crawls

    def get_crawl(self, name: str) -> dict[str, Any]:
        crawl: dict[str, Any] = self._request("GET", f"/admin/v1/crawls/{name}")
        return crawl

    def put_crawl(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        crawl: dict[str, Any] = self._request("PUT", f"/admin/v1/crawls/{name}", json=payload)
        return crawl

    def delete_crawl(self, name: str) -> None:
        self._request("DELETE", f"/admin/v1/crawls/{name}")

    def start_crawl(self, name: str) -> dict[str, Any]:
        started: dict[str, Any] = self._request("POST", f"/admin/v1/crawls/{name}/fetch")
        return started

    def start_extract(self, name: str) -> dict[str, Any]:
        started: dict[str, Any] = self._request("POST", f"/admin/v1/crawls/{name}/extract")
        return started

    def load_crawl(self, name: str) -> dict[str, Any]:
        """Load the crawl's latest extracted snapshot into the database."""
        started: dict[str, Any] = self._request("POST", f"/admin/v1/crawls/{name}/process")
        return started

    def run_crawl_pipeline(self, name: str) -> dict[str, Any]:
        """Crawl, extract and load the crawl in one unattended chain."""
        started: dict[str, Any] = self._request("POST", f"/admin/v1/crawls/{name}/run")
        return started


@dataclass
class DataAPI(_BaseAPI):
    """Client for a read-only consumer API app (gold = curated, silver = staging)."""

    @classmethod
    def gold(cls) -> DataAPI:
        from django.conf import settings

        return cls(base_url=settings.GOLD_API_URL)

    @classmethod
    def silver(cls) -> DataAPI:
        from django.conf import settings

        return cls(base_url=settings.SILVER_API_URL)

    def stats(self) -> dict[str, Any]:
        stats: dict[str, Any] = self._request("GET", "/v1/stats")
        return stats

    def list_entities(
        self,
        q: str | None = None,
        kind: str | None = None,
        page: int = 1,
        limit: int = 20,
        order: str = "label",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "limit": limit, "order": order}
        if q:
            params["q"] = q
        if kind:
            params["kind"] = kind
        result: dict[str, Any] = self._request("GET", "/v1/entities", params=params)
        return result

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        entity: dict[str, Any] = self._request("GET", f"/v1/entities/{entity_id}")
        return entity

    def list_people(
        self, role: str, q: str | None = None, page: int = 1, limit: int = 20, sort: str = "label"
    ) -> dict[str, Any]:
        """People by role: ``role`` is "composers", "soloists", or "conductors"."""
        params: dict[str, Any] = {"page": page, "limit": limit, "sort": sort}
        if q:
            params["q"] = q
        result: dict[str, Any] = self._request("GET", f"/v1/{role}", params=params)
        return result

    def person_concerts(self, person_id: str, page: int = 1, limit: int = 20) -> dict[str, Any]:
        result: dict[str, Any] = self._request(
            "GET", f"/v1/people/{person_id}/concerts", params={"page": page, "limit": limit}
        )
        return result

    def list_concerts(
        self, q: str | None = None, source: str | None = None, page: int = 1, limit: int = 20
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "limit": limit}
        if q:
            params["q"] = q
        if source:
            params["source"] = source
        result: dict[str, Any] = self._request("GET", "/v1/concerts", params=params)
        return result

    def get_concert(self, concert_id: int) -> dict[str, Any]:
        concert: dict[str, Any] = self._request("GET", f"/v1/concerts/{concert_id}")
        return concert

    def person_recordings(self, person_id: str, page: int = 1, limit: int = 20) -> dict[str, Any]:
        result: dict[str, Any] = self._request(
            "GET", f"/v1/people/{person_id}/recordings", params={"page": page, "limit": limit}
        )
        return result

    def list_recordings(
        self, q: str | None = None, source: str | None = None, page: int = 1, limit: int = 20
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "limit": limit}
        if q:
            params["q"] = q
        if source:
            params["source"] = source
        result: dict[str, Any] = self._request("GET", "/v1/recordings", params=params)
        return result

    def get_recording(self, recording_id: int) -> dict[str, Any]:
        recording: dict[str, Any] = self._request("GET", f"/v1/recordings/{recording_id}")
        return recording

    def list_works(
        self,
        q: str | None = None,
        page: int = 1,
        limit: int = 20,
        performed_only: bool = False,
        sort: str = "label",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "limit": limit, "sort": sort}
        if q:
            params["q"] = q
        if performed_only:
            params["performed"] = "true"
        result: dict[str, Any] = self._request("GET", "/v1/works", params=params)
        return result

    def list_mentions(self, status: str | None = None, page: int = 1, limit: int = 20) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "limit": limit}
        if status:
            params["status"] = status
        result: dict[str, Any] = self._request("GET", "/v1/mentions", params=params)
        return result
