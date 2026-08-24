"""Tests for the classicfm HTTP fetch layer.

Both index pages are single documents with no pagination, so what matters
here is simply that the right two URLs are requested, in order, with a
politeness delay between them. Retries are ``composer_http.get_text``'s job
and are tested in that package.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from composer_scrapers.classicfm.fetch import ARTISTS_URL, COMPOSERS_URL, _make_client, fetch_index_pages


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    class _MockedClient(httpx.Client):
        def __init__(self, **kw: Any) -> None:
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(**kw)

    monkeypatch.setattr("composer_scrapers.classicfm.fetch.httpx.Client", _MockedClient)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Politeness delays are real seconds; drop them for the suite."""
    monkeypatch.setattr("composer_scrapers.classicfm.fetch.time.sleep", lambda _: None)
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)


PAGES: dict[str, str] = {
    "/composers/": "<html>composers index</html>",
    "/artists/": "<html>artists index</html>",
}


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path in PAGES:
        return httpx.Response(200, text=PAGES[request.url.path])
    return httpx.Response(404, text="not found")


def test_fetch_index_pages_returns_both_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _handler)
    composers_html, artists_html = fetch_index_pages()
    assert composers_html == "<html>composers index</html>"
    assert artists_html == "<html>artists index</html>"


def test_fetch_index_pages_requests_composers_then_artists(monkeypatch: pytest.MonkeyPatch) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return _handler(request)

    _patch_client(monkeypatch, handler)
    fetch_index_pages()
    assert requested == ["/composers/", "/artists/"]


def test_fetch_index_pages_sleeps_between_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("composer_scrapers.classicfm.fetch.time.sleep", sleeps.append)

    _patch_client(monkeypatch, _handler)
    fetch_index_pages()
    assert sleeps == [0.5]


def test_make_client_sends_a_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    with _make_client() as client:
        assert client.headers["User-Agent"]


def test_urls_target_classicfm() -> None:
    assert COMPOSERS_URL == "https://www.classicfm.com/composers/"
    assert ARTISTS_URL == "https://www.classicfm.com/artists/"
