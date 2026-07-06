"""Tests for the Berlin Philharmonic HTTP fetch layer."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from composer_ingest.scraper.sources.berlinphil.fetch import _concert_ids, _fetch_json, iter_concerts


def _concerts_payload(ids: list[str]) -> dict[str, Any]:
    return {"_links": {"concert": [{"id": cid} for cid in ids]}}


# ---------------------------------------------------------------------------
# _fetch_json
# ---------------------------------------------------------------------------


def test_fetch_json_returns_parsed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"title": "Beethoven Night"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = _fetch_json(client, "test", "concert/123")

    assert result == {"title": "Beethoven Night"}


def test_fetch_json_retries_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_ingest.scraper.sources.berlinphil.fetch.time.sleep", lambda _: None)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503, text="Unavailable")
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = _fetch_json(client, "test", "concerts")

    assert len(attempts) == 3
    assert result == {"ok": True}


def test_fetch_json_raises_after_all_retries_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_ingest.scraper.sources.berlinphil.fetch.time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Always failing")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            _fetch_json(client, "test", "concerts")


# ---------------------------------------------------------------------------
# _concert_ids
# ---------------------------------------------------------------------------


def test_concert_ids_parses_links_block() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_concerts_payload(["111", "222", "333"]))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ids = _concert_ids(client)

    assert ids == ["111", "222", "333"]


def test_concert_ids_skips_entries_without_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"_links": {"concert": [{"href": "/v2/concert/no-id"}, {"id": "42"}]}}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ids = _concert_ids(client)

    assert ids == ["42"]


def test_concert_ids_empty_when_concert_key_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"_links": {}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ids = _concert_ids(client)

    assert ids == []


# ---------------------------------------------------------------------------
# iter_concerts
# ---------------------------------------------------------------------------


def test_iter_concerts_yields_detail_for_each_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_ingest.scraper.sources.berlinphil.fetch.time.sleep", lambda _: None)

    def fake_fetch(client: Any, label: str, path: str) -> Any:
        if path == "concerts":
            return _concerts_payload(["10", "20"])
        return {"id": path.split("/")[-1]}

    monkeypatch.setattr("composer_ingest.scraper.sources.berlinphil.fetch._fetch_json", fake_fetch)
    concerts = list(iter_concerts())

    assert len(concerts) == 2
    assert concerts[0] == {"id": "10"}
    assert concerts[1] == {"id": "20"}


def test_iter_concerts_respects_max_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_ingest.scraper.sources.berlinphil.fetch.time.sleep", lambda _: None)
    fetched_paths: list[str] = []

    def fake_fetch(client: Any, label: str, path: str) -> Any:
        fetched_paths.append(path)
        if path == "concerts":
            return _concerts_payload(["1", "2", "3", "4", "5"])
        return {"id": path.split("/")[-1]}

    monkeypatch.setattr("composer_ingest.scraper.sources.berlinphil.fetch._fetch_json", fake_fetch)
    list(iter_concerts(max_pages=2))

    detail_fetches = [p for p in fetched_paths if p.startswith("concert/")]
    assert len(detail_fetches) == 2
