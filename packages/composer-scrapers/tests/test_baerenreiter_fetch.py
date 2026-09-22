"""Tests for the Bärenreiter fetch layer: sitemap ids and the per-product sweep.

Retries are ``composer_http.get_text``'s job and are tested there; what is
Bärenreiter's own is reading the namespaced sitemap and surviving a bad product.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from composer_http import PageCache
from composer_scrapers.baerenreiter import fetch
from composer_scrapers.baerenreiter.fetch import api_url, iter_products, product_ids

SITEMAP = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<ns4:urlset xmlns:ns4="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<ns4:url><ns4:loc>https://www.baerenreiter.com/en/product/BA05474D</ns4:loc>"
    "<ns4:lastmod>2026-09-22</ns4:lastmod></ns4:url>"
    "<ns4:url><ns4:loc>https://www.baerenreiter.com/en/product/BA11314-65</ns4:loc></ns4:url>"
    "<ns4:url><ns4:loc>https://www.baerenreiter.com/en/product/BA05474D</ns4:loc></ns4:url>"
    "</ns4:urlset>"
)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Politeness delays are real seconds; drop them for the suite."""
    monkeypatch.setattr("composer_scrapers.baerenreiter.fetch.time.sleep", lambda _: None)
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sitemap_ids_are_read_through_the_namespace_prefix_and_deduplicated() -> None:
    assert product_ids(SITEMAP) == ["BA05474D", "BA11314-65"]


def test_a_failing_product_is_skipped_and_the_sweep_goes_on() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        product_id = request.url.path.rsplit("/", 1)[1]
        requested.append(product_id)
        if product_id == "BAD":
            return httpx.Response(404)
        if product_id == "HTML":
            return httpx.Response(200, text="<html></html>")
        return httpx.Response(200, json={"id": product_id, "title": "T"})

    with _client(handler) as client:
        got = [pid for pid, _ in iter_products(client, ["A", "BAD", "HTML", "B"])]

    assert got == ["A", "B"]
    assert requested.count("BAD") >= 1


def test_max_pages_caps_the_products_requested() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "X", "title": "T"})

    with _client(handler) as client:
        assert len(list(iter_products(client, ["A", "B", "C"], max_pages=2))) == 2


def test_a_mirrored_product_is_read_without_a_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = PageCache(tmp_path / "pages.db")
    cache.put(api_url("A"), json.dumps({"id": "A", "title": "Mirrored"}))
    slept: list[float] = []
    monkeypatch.setattr(fetch.time, "sleep", slept.append)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request to {request.url}")

    with _client(handler) as client:
        got = list(iter_products(client, ["A"], cache))

    assert got == [("A", {"id": "A", "title": "Mirrored"})]
    assert slept == []


def test_only_a_readable_answer_is_mirrored(tmp_path: Path) -> None:
    cache = PageCache(tmp_path / "pages.db")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/GOOD"):
            return httpx.Response(200, json={"id": "GOOD", "title": "T"})
        return httpx.Response(200, text="not json")

    with _client(handler) as client:
        list(iter_products(client, ["GOOD", "BROKEN"], cache))

    assert cache.get(api_url("GOOD")) is not None
    assert cache.get(api_url("BROKEN")) is None
