"""Tests for the imslp_works HTTP layer: the bulk worklist, and the bounded
detail pass over it.

The response shapes mirror what the two IMSLP endpoints answer live. The
worklist is the awkward one — rows keyed by stringified index alongside a
``metadata`` entry carrying the pagination flag, with the fields that identify
a work buried in ``intvals`` — and the detail endpoint is MediaWiki 1.18, which
reports an unknown page id as a 200 with an error object rather than a 404.
A stale worklist row pointing at a deleted page is not hypothetical: the live
catalogue has them.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from composer_scrapers.imslp_works.fetch import (
    BASE_URL,
    WorkRow,
    iter_worklist,
    iter_works,
    parse_url,
)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Politeness delays are real seconds; drop them for the suite."""
    monkeypatch.setattr("composer_scrapers.imslp_works.fetch.time.sleep", lambda _: None)
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)


def _work(index: int, page_id: int, title: str, composer: str, icatno: str = "") -> dict[str, Any]:
    return {
        "id": title,
        "type": "2",
        "intvals": {
            "composer": composer,
            "worktitle": title.split(" (")[0],
            "icatno": icatno,
            "pageid": str(page_id),
        },
        "permlink": f"{BASE_URL}/wiki/{title.replace(' ', '_')}",
    }


def _worklist(rows: list[dict[str, Any]], more: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {str(i): row for i, row in enumerate(rows)}
    body["metadata"] = {"start": 0, "limit": 1000, "moreresultsavailable": more}
    return body


def _parsed(document: str) -> dict[str, Any]:
    return {"parse": {"title": "ignored", "text": {"*": document}}}


PAGE_ONE = _worklist(
    [
        _work(0, 101, "Sonata (Alpha, A)", "Alpha, A", "IAA 1"),
        _work(1, 102, "Rondo (Beta, B)", "Beta, B"),
    ],
    more=True,
)
PAGE_TWO = _worklist([_work(0, 103, "Fugue (Gamma, G)", "Gamma, G", "IGG 3")])

INFOBOX = "<table><tr><th>Instrumentation</th><td>organ</td></tr></table>"


def _handler(details: dict[int, httpx.Response] | None = None) -> Any:
    pages = details if details is not None else {}

    def handle(request: httpx.Request) -> httpx.Response:
        if "API.ISCR.php" in request.url.path:
            start = request.url.query.decode()
            return httpx.Response(200, json=PAGE_TWO if "start=1000" in start else PAGE_ONE)
        page_id = int(request.url.params["pageid"])
        return pages.get(page_id, httpx.Response(200, json=_parsed(INFOBOX)))

    return handle


def _use(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Point the source's client at *handler*, with the page mirror off."""
    monkeypatch.setattr(
        "composer_scrapers.imslp_works.fetch.new_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr("composer_scrapers.imslp_works.fetch.open_page_cache", lambda: None)


def test_a_work_page_is_requested_the_way_imslp_recordings_requests_it() -> None:
    """The mirror is keyed by URL, so the two IMSLP sources only share their
    overlap while they ask for pages identically."""
    from composer_scrapers.imslp_recordings.fetch import parse_url as recordings_parse_url

    assert parse_url(207061) == recordings_parse_url(207061)


class TestWorklist:
    def test_pages_the_bulk_endpoint_until_it_is_exhausted(self) -> None:
        with httpx.Client(transport=httpx.MockTransport(_handler())) as client:
            rows = list(iter_worklist(client))
        assert [row.page_id for row in rows] == [101, 102, 103]

    def test_reads_the_fields_that_identify_a_work_out_of_intvals(self) -> None:
        with httpx.Client(transport=httpx.MockTransport(_handler())) as client:
            first = next(iter(iter_worklist(client)))
        assert first == WorkRow(
            page_id=101,
            title="Sonata (Alpha, A)",
            composer="Alpha, A",
            catalogue_number="IAA 1",
            url=f"{BASE_URL}/wiki/Sonata_(Alpha,_A)",
        )

    def test_an_empty_catalogue_number_is_no_catalogue_number(self) -> None:
        with httpx.Client(transport=httpx.MockTransport(_handler())) as client:
            rows = {row.page_id: row for row in iter_worklist(client)}
        assert rows[102].catalogue_number is None

    def test_a_row_missing_what_identifies_a_work_is_skipped(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            rows = [{"id": "No intvals"}, {"id": "T (C, C)", "intvals": {"composer": "C, C"}}]
            body = _worklist([])
            body.update({str(i): row for i, row in enumerate(rows)})
            body["2"] = _work(2, 900, "Kept (D, D)", "D, D")
            return httpx.Response(200, json=body)

        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            assert [row.page_id for row in iter_worklist(client)] == [900]


class TestDetailPass:
    def test_every_work_is_yielded_even_when_nothing_is_enriched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The catalogue is the point; the detail pass is an optional second one."""
        _use(monkeypatch, _handler())
        results = list(iter_works(max_details=0))
        assert [row.page_id for row, _ in results] == [101, 102, 103]
        assert all(document is None for _, document in results)

    def test_max_details_bounds_the_fetching_not_the_catalogue(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _use(monkeypatch, _handler())
        results = list(iter_works(max_details=2))
        assert len(results) == 3
        assert [document is not None for _, document in results] == [True, True, False]

    def test_an_uncapped_run_enriches_everything(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _use(monkeypatch, _handler())
        assert all(document is not None for _, document in iter_works())

    def test_a_stale_worklist_row_costs_its_page_not_the_sweep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """1.18 answers a deleted page id with a 200 and an error object."""
        gone = httpx.Response(200, json={"error": {"code": "nosuchpageid", "info": "no page 102"}})
        _use(monkeypatch, _handler({102: gone}))
        results = list(iter_works())
        assert [row.page_id for row, _ in results] == [101, 102, 103]
        assert [document is not None for _, document in results] == [True, False, True]

    def test_an_http_error_costs_its_page_not_the_sweep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _use(monkeypatch, _handler({102: httpx.Response(500)}))
        assert [document is not None for _, document in iter_works()] == [True, False, True]

    def test_a_failing_page_still_spends_its_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Counting successes would let a run of failures outgrow its budget."""
        _use(monkeypatch, _handler({101: httpx.Response(500)}))
        results = list(iter_works(max_details=1))
        assert [document is not None for _, document in results] == [False, False, False]


class TestPageMirror:
    def test_a_mirrored_page_is_not_fetched_twice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int] = []

        def handle(request: httpx.Request) -> httpx.Response:
            if "API.ISCR.php" in request.url.path:
                return httpx.Response(200, json=_worklist([_work(0, 101, "S (A, A)", "A, A")]))
            calls.append(int(request.url.params["pageid"]))
            return httpx.Response(200, json=_parsed(INFOBOX))

        _use(monkeypatch, handle)
        cache = _FakeCache()
        monkeypatch.setattr("composer_scrapers.imslp_works.fetch.open_page_cache", lambda: cache)
        assert [d is not None for _, d in iter_works()] == [True]
        assert [d is not None for _, d in iter_works()] == [True]
        assert calls == [101]

    def test_a_page_that_failed_is_not_mirrored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Storing an error would mean never retrying the page that produced it."""

        def handle(request: httpx.Request) -> httpx.Response:
            if "API.ISCR.php" in request.url.path:
                return httpx.Response(200, json=_worklist([_work(0, 101, "S (A, A)", "A, A")]))
            return httpx.Response(200, json={"error": {"code": "nosuchpageid"}})

        _use(monkeypatch, handle)
        cache = _FakeCache()
        monkeypatch.setattr("composer_scrapers.imslp_works.fetch.open_page_cache", lambda: cache)
        assert [d is not None for _, d in iter_works()] == [False]
        assert cache.stored == {}


class _FakeCache:
    """Enough of PageCache to see what a sweep would have mirrored."""

    def __init__(self) -> None:
        self.stored: dict[str, str] = {}

    def get(self, url: str) -> str | None:
        return self.stored.get(url)

    def put(self, url: str, body: str) -> None:
        self.stored[url] = body

    def summary(self) -> str:
        return f"{len(self.stored)} stored"
