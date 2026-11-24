"""HTTP clients for the two FastAPI apps — the dashboard's only I/O.

The dashboard deliberately has no database connection; scraping and loading go
through the admin API (``/admin/v1``), data inspection through the read-only
consumer API (``/v1``).
"""

from __future__ import annotations

import atexit
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from .schemas import (
    ConcertDetail,
    ConcertListPage,
    EnsemblePage,
    EntityDetail,
    EntityPage,
    FetchStarted,
    GoldStatus,
    MentionPage,
    PersonConcertsPage,
    PersonPage,
    PersonRecordingsPage,
    PromoteOptions,
    RecordingDetail,
    RecordingListPage,
    Rule1Config,
    RunOut,
    RunStarted,
    ScraperOut,
    SnapshotOut,
    StatsOut,
    WorkDetail,
    WorkPage,
)

TIMEOUT = 10.0

_CLIENTS: dict[tuple[str, str | None], httpx.Client] = {}


def _close_clients() -> None:
    for client in list(_CLIENTS.values()):
        if not client.is_closed:
            client.close()
    _CLIENTS.clear()


atexit.register(_close_clients)


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
    _client: httpx.Client | None = field(default=None, init=False, repr=False, compare=False)

    def _get_client(self) -> httpx.Client:
        headers = {"X-Admin-Key": self.api_key} if self.api_key else {}
        if self.transport is not None:
            if self._client is None or self._client.is_closed:
                self._client = httpx.Client(
                    base_url=self.base_url, headers=headers, timeout=TIMEOUT, transport=self.transport
                )
            return self._client
        key = (self.base_url, self.api_key)
        client = _CLIENTS.get(key)
        if client is None or client.is_closed:
            client = httpx.Client(base_url=self.base_url, headers=headers, timeout=TIMEOUT)
            _CLIENTS[key] = client
        return client

    def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._get_client().request(method, path, **kwargs)
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

    def _model[T: BaseModel](self, schema: type[T], method: str, path: str, **kwargs: Any) -> T:
        """Decode and validate a JSON object response into ``schema``."""
        data = self._request(method, path, **kwargs)
        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            raise AdminAPIError(f"Unexpected API response from {path}: {exc}") from exc

    def _model_list[T: BaseModel](self, schema: type[T], method: str, path: str, **kwargs: Any) -> list[T]:
        """Decode and validate a JSON array response into ``list[schema]``."""
        data = self._request(method, path, **kwargs)
        if not isinstance(data, list):
            raise AdminAPIError(f"Unexpected API response from {path}: expected a JSON array")
        try:
            return [schema.model_validate(item) for item in data]
        except ValidationError as exc:
            raise AdminAPIError(f"Unexpected API response from {path}: {exc}") from exc


@dataclass
class AdminAPI(_BaseAPI):
    """Client for the admin API: manage scrapers, snapshots, and loads."""

    @classmethod
    def from_env(cls) -> AdminAPI:
        from django.conf import settings

        return cls(base_url=settings.ADMIN_API_URL, api_key=settings.ADMIN_API_KEY)

    def list_scrapers(self) -> list[ScraperOut]:
        return self._model_list(ScraperOut, "GET", "/admin/v1/scrapers")

    def fetch_scraper(self, name: str) -> FetchStarted:
        return self._model(FetchStarted, "POST", f"/admin/v1/scrapers/{name}/fetch")

    def fetch_due(self) -> list[FetchStarted]:
        return self._model_list(FetchStarted, "POST", "/admin/v1/scrapers/fetch-due")

    def list_snapshots(self) -> list[SnapshotOut]:
        return self._model_list(SnapshotOut, "GET", "/admin/v1/snapshots")

    def process_snapshot(self, source: str, snapshot_id: str) -> RunStarted:
        return self._model(RunStarted, "POST", f"/admin/v1/snapshots/{source}/{snapshot_id}/process")

    def abandon_snapshot(self, source: str, snapshot_id: str) -> SnapshotOut:
        """Mark a snapshot stuck on ``running`` as failed, unblocking its source."""
        return self._model(SnapshotOut, "POST", f"/admin/v1/snapshots/{source}/{snapshot_id}/abandon")

    def list_runs(self, limit: int = 20) -> list[RunOut]:
        return self._model_list(RunOut, "GET", "/admin/v1/runs", params={"limit": limit})

    def gold_status(self) -> GoldStatus:
        return self._model(GoldStatus, "GET", "/admin/v1/gold")

    def start_promote(self, options: PromoteOptions | None = None) -> GoldStatus:
        kwargs: dict[str, Any] = {"json": options} if options else {}
        return self._model(GoldStatus, "POST", "/admin/v1/promote", **kwargs)

    def get_rule1_config(self) -> Rule1Config:
        return self._model(Rule1Config, "GET", "/admin/v1/rule1-config")

    def put_rule1_config(self, payload: Rule1Config) -> Rule1Config:
        return self._model(Rule1Config, "PUT", "/admin/v1/rule1-config", json=payload.model_dump())


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

    def stats(self) -> StatsOut:
        return self._model(StatsOut, "GET", "/v1/stats")

    def list_entities(
        self,
        q: str | None = None,
        kind: str | None = None,
        page: int = 1,
        limit: int = 20,
        order: str = "label",
    ) -> EntityPage:
        params: dict[str, Any] = {"page": page, "limit": limit, "order": order}
        if q:
            params["q"] = q
        if kind:
            params["kind"] = kind
        return self._model(EntityPage, "GET", "/v1/entities", params=params)

    def get_entity(self, entity_id: str) -> EntityDetail:
        return self._model(EntityDetail, "GET", f"/v1/entities/{entity_id}")

    def list_people(
        self, role: str, filters: Filters = NO_FILTERS, page: int = 1, limit: int = 20, sort: str = "label"
    ) -> PersonPage:
        """People by role: ``role`` is "composers", "soloists", or "conductors"."""
        params = filters.add_to({"page": page, "limit": limit, "sort": sort})
        return self._model(PersonPage, "GET", f"/v1/{role}", params=params)

    def list_ensembles(
        self,
        filters: Filters = NO_FILTERS,
        anchored: bool | None = None,
        page: int = 1,
        limit: int = 20,
        sort: str = "label",
    ) -> EnsemblePage:
        """Ensembles with type and home city; ``anchored=False`` keeps the ones
        no Wikidata item accounts for."""
        params = filters.add_to({"page": page, "limit": limit, "sort": sort})
        if anchored is not None:
            params["anchored"] = "true" if anchored else "false"
        return self._model(EnsemblePage, "GET", "/v1/ensembles", params=params)

    def person_concerts(self, person_id: str, page: int = 1, limit: int = 20) -> PersonConcertsPage:
        params = {"page": page, "limit": limit}
        return self._model(PersonConcertsPage, "GET", f"/v1/people/{person_id}/concerts", params=params)

    def list_concerts(self, filters: Filters = NO_FILTERS, page: int = 1, limit: int = 20) -> ConcertListPage:
        params = filters.add_to({"page": page, "limit": limit})
        return self._model(ConcertListPage, "GET", "/v1/concerts", params=params)

    def get_concert(self, concert_id: uuid.UUID) -> ConcertDetail:
        return self._model(ConcertDetail, "GET", f"/v1/concerts/{concert_id}")

    def person_recordings(self, person_id: str, page: int = 1, limit: int = 20) -> PersonRecordingsPage:
        params = {"page": page, "limit": limit}
        return self._model(PersonRecordingsPage, "GET", f"/v1/people/{person_id}/recordings", params=params)

    def list_recordings(
        self, filters: Filters = NO_FILTERS, page: int = 1, limit: int = 20
    ) -> RecordingListPage:
        params = filters.add_to({"page": page, "limit": limit})
        return self._model(RecordingListPage, "GET", "/v1/recordings", params=params)

    def get_recording(self, recording_id: uuid.UUID) -> RecordingDetail:
        return self._model(RecordingDetail, "GET", f"/v1/recordings/{recording_id}")

    def list_works(
        self,
        filters: Filters = NO_FILTERS,
        page: int = 1,
        limit: int = 20,
        performed_only: bool = False,
        sort: str = "label",
    ) -> WorkPage:
        params = filters.add_to({"page": page, "limit": limit, "sort": sort})
        if performed_only:
            params["performed"] = "true"
        return self._model(WorkPage, "GET", "/v1/works", params=params)

    def get_work(self, work_id: str) -> WorkDetail:
        return self._model(WorkDetail, "GET", f"/v1/works/{work_id}")

    def list_mentions(self, status: str | None = None, page: int = 1, limit: int = 20) -> MentionPage:
        params: dict[str, Any] = {"page": page, "limit": limit}
        if status:
            params["status"] = status
        return self._model(MentionPage, "GET", "/v1/mentions", params=params)
