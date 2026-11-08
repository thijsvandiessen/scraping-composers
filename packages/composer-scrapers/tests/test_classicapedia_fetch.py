"""Tests for the Classicapedia HTTP layer: the sweep and the Wikipedia/Wikidata lookups."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from composer_http import PageCache
from composer_http.testing import mock_session
from composer_scrapers.classicapedia.fetch import PageRef, fetch_page, iter_pages
from composer_scrapers.classicapedia.resolve import search_people, wikidata_items, wikipedia_qids

PROFILE = '<article class="ficha"><h1>Haydn</h1></article>'
URL = "https://classicapedia.com/en/b/joseph-haydn.html"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)


def _cache(tmp_path: Path) -> PageCache:
    return PageCache(tmp_path / "pages.db")


def test_a_profile_is_mirrored_and_an_error_page_is_not(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, text=PROFILE if "haydn" in request.url.path else "<html>Oops</html>")

    with mock_session(handler, cache=_cache(tmp_path)) as session:
        assert fetch_page(session, URL) == PROFILE
        assert fetch_page(session, URL) == PROFILE
        bad = "https://classicapedia.com/en/b/nobody.html"
        assert fetch_page(session, bad) is None
        assert fetch_page(session, bad) is None
    assert calls.count("/en/b/joseph-haydn.html") == 1
    assert calls.count("/en/b/nobody.html") == 2


def test_the_sweep_survives_a_failed_page_and_honours_the_cap() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "broken" in request.url.path:
            return httpx.Response(500)
        return httpx.Response(200, text=PROFILE)

    refs = [
        PageRef("b", slug, f"https://classicapedia.com/en/b/{slug}.html") for slug in ("broken", "a", "b")
    ]
    with mock_session(handler) as session:
        assert [ref.slug for ref, _ in iter_pages(session, refs)] == ["a", "b"]
        assert [ref.slug for ref, _ in iter_pages(session, refs, max_pages=2)] == ["a"]


def test_titles_follow_normalisation_and_redirects_to_a_qid() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "en.wikipedia.org"
        return httpx.Response(
            200,
            json={
                "query": {
                    "normalized": [{"from": "la Scala", "to": "La Scala"}],
                    "redirects": [{"from": "La Scala", "to": "Teatro alla Scala"}],
                    "pages": {
                        "1": {"title": "Teatro alla Scala", "pageprops": {"wikibase_item": "Q5471"}},
                        "2": {"title": "Musikverein", "pageprops": {"wikibase_item": "Q686720"}},
                        "-1": {"title": "Nothing", "missing": ""},
                    },
                }
            },
        )

    articles = [("en", "la Scala"), ("en", "Musikverein"), ("en", "Nothing")]
    with mock_session(handler) as session:
        assert wikipedia_qids(session, articles) == {
            ("en", "la Scala"): "Q5471",
            ("en", "Musikverein"): "Q686720",
        }


def test_each_language_asks_its_own_wikipedia_in_batches_of_fifty() -> None:
    asked: list[tuple[str, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        titles = parse_qs(urlparse(str(request.url)).query)["titles"][0].split("|")
        asked.append((request.url.host, len(titles)))
        return httpx.Response(200, json={"query": {"pages": {}}})

    articles = [("es", f"T{n}") for n in range(51)] + [("en", "One")]
    with mock_session(handler) as session:
        wikipedia_qids(session, articles)
    assert asked == [("en.wikipedia.org", 1), ("es.wikipedia.org", 50), ("es.wikipedia.org", 1)]


def test_a_failed_lookup_costs_identities_and_not_the_run() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with mock_session(handler) as session:
        assert wikipedia_qids(session, [("en", "La Scala")]) == {}
        assert wikidata_items(session, ["Q5471"]) == {}


def test_a_missing_item_is_left_out() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "entities": {"Q1": {"labels": {"en": {"value": "One"}}, "claims": {}}, "Q2": {"missing": ""}}
            },
        )

    with mock_session(handler) as session:
        items = wikidata_items(session, ["Q1", "Q2", "Q1"])
    assert set(items) == {"Q1"}
    assert items["Q1"].label == "One"


def test_a_name_search_asks_wikidata_and_returns_the_qids(tmp_path: Path) -> None:
    asked: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(parse_qs(urlparse(str(request.url)).query))
        return httpx.Response(200, json={"search": [{"id": "Q1"}, {"id": "Q2"}, {"label": "no id"}]})

    with mock_session(handler, cache=_cache(tmp_path)) as session:
        assert search_people(session, "Josef Suk") == ["Q1", "Q2"]
    assert asked[0]["action"] == ["wbsearchentities"]
    assert asked[0]["search"] == ["Josef Suk"]


def test_a_failed_search_finds_nobody(tmp_path: Path) -> None:
    with mock_session(lambda request: httpx.Response(404), cache=_cache(tmp_path)) as session:
        assert search_people(session, "Josef Suk") == []
