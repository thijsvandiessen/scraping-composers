"""HTTP clients for the two FastAPI apps — the dashboard's only I/O.

The dashboard deliberately has no database connection; scraping and loading go
through the admin API (``/admin/v1``), data inspection through the read-only
consumer API (``/v1``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import httpx

TIMEOUT = 10.0


class AdminAPIError(Exception):
    """A failed API call, with a message fit to show on the page."""


@dataclass(frozen=True)
class Filters:
    """The ``q`` (free text) and ``source`` (scraper name) filters the list
    endpoints share. Grouped so the client methods stay under the argument cap,
    mirroring ``composer_api.deps.Filters`` on the other side of the wire."""

    q: str | None = None
    source: str | None = None

    def add_to(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.q:
            params["q"] = self.q
        if self.source:
            params["source"] = self.source
        return params


NO_FILTERS = Filters()


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

    def _json_dict(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """A response the API documents as an object.

        The two casts below are what lets every method below be a single
        ``return`` line: ``_request`` hands back ``Any`` (the decoded JSON),
        and pyright's strict mode wants that pinned to a real type exactly
        once rather than at each of the twenty-odd call sites.
        """
        return cast(dict[str, Any], self._request(method, path, **kwargs))

    def _json_list(self, method: str, path: str, **kwargs: Any) -> list[dict[str, Any]]:
        """A response the API documents as an array of objects."""
        return cast(list[dict[str, Any]], self._request(method, path, **kwargs))


@dataclass
class AdminAPI(_BaseAPI):
    """Client for the admin API: manage scrapers, snapshots, and loads."""

    @classmethod
    def from_env(cls) -> AdminAPI:
        from django.conf import settings

        return cls(base_url=settings.ADMIN_API_URL, api_key=settings.ADMIN_API_KEY)

    def list_scrapers(self) -> list[dict[str, Any]]:
        return self._json_list("GET", "/admin/v1/scrapers")

    def fetch_scraper(self, name: str) -> dict[str, Any]:
        return self._json_dict("POST", f"/admin/v1/scrapers/{name}/fetch")

    def fetch_due(self) -> list[dict[str, Any]]:
        return self._json_list("POST", "/admin/v1/scrapers/fetch-due")

    def list_snapshots(self) -> list[dict[str, Any]]:
        return self._json_list("GET", "/admin/v1/snapshots")

    def process_snapshot(self, source: str, snapshot_id: str) -> dict[str, Any]:
        return self._json_dict("POST", f"/admin/v1/snapshots/{source}/{snapshot_id}/process")

    def abandon_snapshot(self, source: str, snapshot_id: str) -> dict[str, Any]:
        """Mark a snapshot stuck on ``running`` as failed, unblocking its source."""
        return self._json_dict("POST", f"/admin/v1/snapshots/{source}/{snapshot_id}/abandon")

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._json_list("GET", "/admin/v1/runs", params={"limit": limit})

    def gold_status(self) -> dict[str, Any]:
        return self._json_dict("GET", "/admin/v1/gold")

    def start_promote(self, options: dict[str, Any] | None = None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"json": options} if options else {}
        return self._json_dict("POST", "/admin/v1/promote", **kwargs)

    def get_rule1_config(self) -> dict[str, Any]:
        return self._json_dict("GET", "/admin/v1/rule1-config")

    def put_rule1_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._json_dict("PUT", "/admin/v1/rule1-config", json=payload)

    def list_crawls(self) -> list[dict[str, Any]]:
        return self._json_list("GET", "/admin/v1/crawls")

    def get_crawl(self, name: str) -> dict[str, Any]:
        return self._json_dict("GET", f"/admin/v1/crawls/{name}")

    def put_crawl(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._json_dict("PUT", f"/admin/v1/crawls/{name}", json=payload)

    def delete_crawl(self, name: str) -> None:
        # 204, so there is no body to type — _request is called directly.
        self._request("DELETE", f"/admin/v1/crawls/{name}")

    def start_crawl(self, name: str) -> dict[str, Any]:
        return self._json_dict("POST", f"/admin/v1/crawls/{name}/fetch")

    def start_extract(self, name: str) -> dict[str, Any]:
        return self._json_dict("POST", f"/admin/v1/crawls/{name}/extract")

    def load_crawl(self, name: str) -> dict[str, Any]:
        """Load the crawl's latest extracted snapshot into the database."""
        return self._json_dict("POST", f"/admin/v1/crawls/{name}/process")

    def run_crawl_pipeline(self, name: str) -> dict[str, Any]:
        """Crawl, extract and load the crawl in one unattended chain."""
        return self._json_dict("POST", f"/admin/v1/crawls/{name}/run")


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
        return self._json_dict("GET", "/v1/stats")

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
        return self._json_dict("GET", "/v1/entities", params=params)

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        return self._json_dict("GET", f"/v1/entities/{entity_id}")

    def list_people(
        self, role: str, filters: Filters = NO_FILTERS, page: int = 1, limit: int = 20, sort: str = "label"
    ) -> dict[str, Any]:
        """People by role: ``role`` is "composers", "soloists", or "conductors"."""
        params = filters.add_to({"page": page, "limit": limit, "sort": sort})
        return self._json_dict("GET", f"/v1/{role}", params=params)

    def person_concerts(self, person_id: str, page: int = 1, limit: int = 20) -> dict[str, Any]:
        params = {"page": page, "limit": limit}
        return self._json_dict("GET", f"/v1/people/{person_id}/concerts", params=params)

    def list_concerts(self, filters: Filters = NO_FILTERS, page: int = 1, limit: int = 20) -> dict[str, Any]:
        params = filters.add_to({"page": page, "limit": limit})
        return self._json_dict("GET", "/v1/concerts", params=params)

    def get_concert(self, concert_id: int) -> dict[str, Any]:
        return self._json_dict("GET", f"/v1/concerts/{concert_id}")

    def person_recordings(self, person_id: str, page: int = 1, limit: int = 20) -> dict[str, Any]:
        params = {"page": page, "limit": limit}
        return self._json_dict("GET", f"/v1/people/{person_id}/recordings", params=params)

    def list_recordings(
        self, filters: Filters = NO_FILTERS, page: int = 1, limit: int = 20
    ) -> dict[str, Any]:
        params = filters.add_to({"page": page, "limit": limit})
        return self._json_dict("GET", "/v1/recordings", params=params)

    def get_recording(self, recording_id: int) -> dict[str, Any]:
        return self._json_dict("GET", f"/v1/recordings/{recording_id}")

    def list_works(
        self,
        filters: Filters = NO_FILTERS,
        page: int = 1,
        limit: int = 20,
        performed_only: bool = False,
        sort: str = "label",
    ) -> dict[str, Any]:
        params = filters.add_to({"page": page, "limit": limit, "sort": sort})
        if performed_only:
            params["performed"] = "true"
        return self._json_dict("GET", "/v1/works", params=params)

    def get_work(self, work_id: str) -> dict[str, Any]:
        return self._json_dict("GET", f"/v1/works/{work_id}")

    def list_mentions(self, status: str | None = None, page: int = 1, limit: int = 20) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "limit": limit}
        if status:
            params["status"] = status
        return self._json_dict("GET", "/v1/mentions", params=params)
