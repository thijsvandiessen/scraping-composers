"""Tests for the decca GraphQL layer.

The batching and the mirror are what make a 3575-family sweep survivable, and
the two query arguments are what make its answers non-empty, so both are
asserted against rather than assumed.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from composer_http import PageCache
from composer_scrapers.decca import fetch as decca_fetch
from composer_scrapers.decca.fetch import (
    GRAPHQL_URL,
    fetch_artists,
    fetch_families,
    fetch_sitemap,
    make_client,
)
from composer_scrapers.decca.urls import SITEMAP_URL


@pytest.fixture(autouse=True)
def no_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(decca_fetch.time, "sleep", lambda _: None)


def _client(handler: Any, seen: list[httpx.Request] | None = None) -> httpx.Client:
    def transport(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(transport))


def _query(request: httpx.Request) -> str:
    body: dict[str, Any] = json.loads(request.content)
    return str(body["query"])


def _answer(**nodes: Any) -> Any:
    """A handler answering with the given aliases under ``universalMusic``."""
    return lambda _request: httpx.Response(200, json={"data": {"universalMusic": dict(nodes)}})


# --------------------------------------------------------------------------- #
# the query
# --------------------------------------------------------------------------- #


def test_the_query_sends_the_channel_and_language_that_populate_credits() -> None:
    """Without ``channel: 0`` the API answers with empty contributor lists — a
    well-formed response with none of the data the fetch exists to get."""
    seen: list[httpx.Request] = []
    with _client(_answer(f7={"idRaw": 7}), seen) as client:
        list(fetch_families(client, [7]))
    query = _query(seen[0])
    assert "channel: 0" in query
    assert 'language: "en"' in query


def test_families_are_batched_into_one_request() -> None:
    ids = list(range(1, decca_fetch.BATCH_SIZE + 1))
    seen: list[httpx.Request] = []
    with _client(_answer(**{f"f{i}": {"idRaw": i} for i in ids}), seen) as client:
        assert len(list(fetch_families(client, ids))) == len(ids)
    assert len(seen) == 1
    query = _query(seen[0])
    for family_id in ids:
        assert f"f{family_id}: productFamily(id: {family_id})" in query


def test_a_batch_larger_than_the_size_is_split() -> None:
    ids = list(range(1, decca_fetch.BATCH_SIZE * 2 + 1))
    seen: list[httpx.Request] = []
    with _client(_answer(**{f"f{i}": {"idRaw": i} for i in ids}), seen) as client:
        list(fetch_families(client, ids))
    assert len(seen) == 2


def test_families_come_back_in_the_order_asked_for() -> None:
    with _client(_answer(f3={"idRaw": 3}, f1={"idRaw": 1}, f2={"idRaw": 2})) as client:
        assert [f["idRaw"] for f in fetch_families(client, [1, 2, 3])] == [1, 2, 3]


def test_an_artist_slug_rides_in_the_argument_not_the_alias() -> None:
    """Roster slugs hold hyphens and leading digits; a GraphQL alias may not."""
    seen: list[httpx.Request] = []
    with _client(_answer(a0={"idRaw": 1}, a1={"idRaw": 2}), seen) as client:
        list(fetch_artists(client, ["von-karajan", "0-9"]))
    query = _query(seen[0])
    assert 'a0: artist(urlAlias: "von-karajan")' in query
    assert 'a1: artist(urlAlias: "0-9")' in query


# --------------------------------------------------------------------------- #
# failure
# --------------------------------------------------------------------------- #


def test_a_family_the_api_does_not_know_is_skipped_not_failed() -> None:
    """A null alias is an unknown id, not an error, and its batch must survive."""
    with _client(_answer(f1={"idRaw": 1}, f2=None, f3={"idRaw": 3})) as client:
        assert [f["idRaw"] for f in fetch_families(client, [1, 2, 3])] == [1, 3]


def test_a_failing_batch_is_halved_so_one_bad_family_costs_only_itself() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = _query(request)
        if "f2:" in query and "f1:" in query:
            return httpx.Response(500)
        aliases = {f"f{i}": ({"idRaw": i} if i != 2 else None) for i in (1, 2, 3, 4) if f"f{i}:" in query}
        return httpx.Response(200, json={"data": {"universalMusic": aliases}})

    with _client(handler) as client:
        found = [f["idRaw"] for f in fetch_families(client, [1, 2, 3, 4])]
    assert found == [1, 3, 4]


def test_a_graphql_error_is_a_failure_even_though_it_arrives_as_http_200() -> None:
    seen: list[httpx.Request] = []
    handler = lambda _r: httpx.Response(200, json={"errors": [{"message": "boom"}]})  # noqa: E731
    with _client(handler, seen) as client:
        assert list(fetch_families(client, [7])) == []
    assert len(seen) > 1  # it was retried before being given up on


# --------------------------------------------------------------------------- #
# the mirror
# --------------------------------------------------------------------------- #


def test_a_mirrored_family_is_served_without_a_request(tmp_path: Any) -> None:
    cache = PageCache(tmp_path / "pages.db")
    cache.put("graphql://decca/family/7", json.dumps({"idRaw": 7, "headline": "mirrored"}))
    seen: list[httpx.Request] = []
    with _client(_answer(f7={"idRaw": 7, "headline": "fetched"}), seen) as client:
        found = list(fetch_families(client, [7], cache))
    assert found[0]["headline"] == "mirrored"
    assert seen == []


def test_only_the_families_missing_from_the_mirror_are_requested(tmp_path: Any) -> None:
    """What makes a sweep resumable: an interrupted run re-asks for the rest."""
    cache = PageCache(tmp_path / "pages.db")
    cache.put("graphql://decca/family/1", json.dumps({"idRaw": 1}))
    seen: list[httpx.Request] = []
    with _client(_answer(f2={"idRaw": 2}, f3={"idRaw": 3}), seen) as client:
        assert [f["idRaw"] for f in fetch_families(client, [1, 2, 3], cache)] == [1, 2, 3]
    query = _query(seen[0])
    assert "f1:" not in query
    assert "f2:" in query and "f3:" in query


def test_what_is_fetched_is_mirrored(tmp_path: Any) -> None:
    cache = PageCache(tmp_path / "pages.db")
    with _client(_answer(f7={"idRaw": 7, "headline": "fetched"})) as client:
        list(fetch_families(client, [7], cache))
    assert json.loads(cache.get("graphql://decca/family/7") or "{}")["headline"] == "fetched"


def test_a_truncated_mirror_entry_is_a_miss_not_a_crash(tmp_path: Any) -> None:
    cache = PageCache(tmp_path / "pages.db")
    cache.put("graphql://decca/family/7", '{"idRaw": 7, "headl')
    with _client(_answer(f7={"idRaw": 7, "headline": "fetched"})) as client:
        assert [f["headline"] for f in fetch_families(client, [7], cache)] == ["fetched"]


def test_artists_are_mirrored_under_their_slug(tmp_path: Any) -> None:
    cache = PageCache(tmp_path / "pages.db")
    with _client(_answer(a0={"idRaw": 1, "screenname": "Alfred Brendel"})) as client:
        list(fetch_artists(client, ["alfredbrendel"], cache))
    assert cache.get("graphql://decca/artist/alfredbrendel") is not None


# --------------------------------------------------------------------------- #
# the sitemap
# --------------------------------------------------------------------------- #

_INDEX = f"<sitemapindex><sitemap><loc>{SITEMAP_URL[:-4]}_0.xml</loc></sitemap></sitemapindex>"


def test_fetch_sitemap_follows_the_index_to_its_urlset() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text=_INDEX if len(requested) == 1 else "<urlset/>")

    with _client(handler) as client:
        assert fetch_sitemap(client) == "<urlset/>"
    assert requested == [SITEMAP_URL, f"{SITEMAP_URL[:-4]}_0.xml"]


def test_an_index_listing_nothing_is_returned_as_is() -> None:
    with _client(lambda _r: httpx.Response(200, text="<urlset/>")) as client:
        assert fetch_sitemap(client) == "<urlset/>"


def test_the_client_identifies_itself() -> None:
    with make_client() as client:
        assert "composer-ingest" in client.headers["user-agent"]


def test_requests_go_to_the_api_host() -> None:
    seen: list[httpx.Request] = []
    with _client(_answer(f7={"idRaw": 7}), seen) as client:
        list(fetch_families(client, [7]))
    assert str(seen[0].url) == GRAPHQL_URL


def test_the_client_allows_a_box_set_time_to_answer() -> None:
    """A batch of box sets outruns the shared 30s default, and a timeout costs
    three retries and two halvings before the family is dropped."""
    with make_client() as client:
        assert client.timeout.read == decca_fetch.TIMEOUT_S
