"""Tests for the IMSLP people source."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from composer_ingest.sources.imslp import fetch_records
from composer_ingest.sources.imslp.fetch import _fetch_page


def _page(*names: str, more: bool = False) -> dict[str, Any]:
    """Build a minimal IMSLP API response page."""
    data: dict[str, Any] = {
        str(i): {"id": f"Category:{name}", "permlink": f"https://imslp.org/{name}"}
        for i, name in enumerate(names)
    }
    data["metadata"] = {"moreresultsavailable": more}
    return data


# ---------------------------------------------------------------------------
# _fetch_page unit tests
# ---------------------------------------------------------------------------


def test_fetch_page_returns_parsed_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_page("Bach, Johann Sebastian", "Beethoven, Ludwig van"))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = _fetch_page(client, start=0)

    assert result["0"]["id"] == "Category:Bach, Johann Sebastian"
    assert result["1"]["id"] == "Category:Beethoven, Ludwig van"
    assert "metadata" in result


def test_fetch_page_includes_start_in_url() -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, json=_page())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        _fetch_page(client, start=2000)

    assert "start=2000" in seen_urls[0]


def test_fetch_page_retries_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_ingest.sources.imslp.fetch.time.sleep", lambda _: None)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(500, text="Server Error")
        return httpx.Response(200, json=_page("Bach, Johann Sebastian"))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = _fetch_page(client, start=0)

    assert len(attempts) == 3
    assert "0" in result


def test_fetch_page_raises_after_all_retries_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_ingest.sources.imslp.fetch.time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="always fails")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            _fetch_page(client, start=0)


# ---------------------------------------------------------------------------
# fetch_records integration tests (network fully mocked via _fetch_page)
# ---------------------------------------------------------------------------


def test_fetch_records_yields_source_records(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_ingest.sources.imslp.time.sleep", lambda _: None)
    page = _page("Bach, Johann Sebastian", "Beethoven, Ludwig van", more=False)

    monkeypatch.setattr("composer_ingest.sources.imslp._fetch_page", lambda client, start: dict(page))

    records = list(fetch_records())
    assert len(records) == 2
    assert records[0].name == "Bach, Johann Sebastian"
    assert records[0].external_id == "Category:Bach, Johann Sebastian"
    assert records[1].name == "Beethoven, Ludwig van"


def test_fetch_records_sets_url_from_permlink(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_ingest.sources.imslp.time.sleep", lambda _: None)
    page = _page("Bach, Johann Sebastian", more=False)

    monkeypatch.setattr("composer_ingest.sources.imslp._fetch_page", lambda client, start: dict(page))

    (record,) = list(fetch_records())
    assert record.url == "https://imslp.org/Bach, Johann Sebastian"


def test_fetch_records_stops_when_no_more_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_ingest.sources.imslp.time.sleep", lambda _: None)
    calls: list[int] = []

    def fake_fetch(client: Any, start: int) -> dict[str, Any]:
        calls.append(start)
        return _page("Composer A", more=False)

    monkeypatch.setattr("composer_ingest.sources.imslp._fetch_page", fake_fetch)

    list(fetch_records())
    assert len(calls) == 1


def test_fetch_records_pages_until_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_ingest.sources.imslp.time.sleep", lambda _: None)
    calls: list[int] = []

    def fake_fetch(client: Any, start: int) -> dict[str, Any]:
        calls.append(start)
        more = len(calls) < 3
        return _page(f"Composer {start}", more=more)

    monkeypatch.setattr("composer_ingest.sources.imslp._fetch_page", fake_fetch)

    list(fetch_records())
    assert len(calls) == 3


def test_fetch_records_stops_at_max_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_ingest.sources.imslp.time.sleep", lambda _: None)
    calls: list[int] = []

    def fake_fetch(client: Any, start: int) -> dict[str, Any]:
        calls.append(start)
        return _page(f"Composer {start}", more=True)

    monkeypatch.setattr("composer_ingest.sources.imslp._fetch_page", fake_fetch)

    list(fetch_records(max_pages=2))
    assert len(calls) == 2


def test_fetch_records_skips_rows_with_empty_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_ingest.sources.imslp.time.sleep", lambda _: None)
    page: dict[str, Any] = {
        "0": {"id": "Category:Valid Name", "permlink": "https://imslp.org/valid"},
        "1": {"id": "Category:", "permlink": None},  # empty after prefix removal
        "2": {"id": "", "permlink": None},  # no category prefix at all
        "metadata": {"moreresultsavailable": False},
    }

    monkeypatch.setattr("composer_ingest.sources.imslp._fetch_page", lambda client, start: dict(page))

    records = list(fetch_records())
    assert len(records) == 1
    assert records[0].name == "Valid Name"
