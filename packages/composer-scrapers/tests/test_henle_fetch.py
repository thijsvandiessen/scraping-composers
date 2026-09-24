"""Tests for the Henle fetch layer: the gzipped sitemap and the per-page sweep.

Retries are ``composer_http``'s job and are tested there; what is Henle's own is
finding the English sitemap, reading product URLs out of it, and surviving a bad
page.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any

import httpx
import pytest
from composer_http import PageCache
from composer_scrapers.henle import fetch
from composer_scrapers.henle.fetch import fetch_sitemap, iter_products, product_urls

SITEMAP_DIR = "https://www.henle.de/sitemap/salesChannel-x"
INDEX = (
    '<?xml version="1.0" encoding="UTF-8"?><sitemapindex>'
    f"<sitemap><loc>{SITEMAP_DIR}/x-sitemap-www-henle-de-1.xml.gz</loc></sitemap>"
    f"<sitemap><loc>{SITEMAP_DIR}/x-sitemap-www-henle-de-en-1.xml.gz</loc></sitemap>"
    "</sitemapindex>"
)
ENGLISH = (
    '<?xml version="1.0" encoding="UTF-8"?><urlset>'
    "<url><loc>https://www.henle.de/en</loc></url>"
    "<url><loc>https://www.henle.de/en/Contact/</loc></url>"
    "<url><loc>https://www.henle.de/en/hn743</loc></url>"
    "<url><loc>https://www.henle.de/en/Waltz-a-minor-op.-34-no.-2/HN-659</loc><lastmod>2026-09-01</lastmod></url>"
    "<url><loc>https://www.henle.de/en/Piano-Sonatas-Volume-I/HN-1</loc></url>"
    "<url><loc>https://www.henle.de/en/Piano-Sonatas-Volume-I-old-slug/HN-1</loc></url>"
    "</urlset>"
)
PRODUCT = '<html><h1 class="product-detail-name">Waltz</h1></html>'


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Politeness delays are real seconds; drop them for the suite."""
    monkeypatch.setattr("composer_scrapers.henle.fetch.time.sleep", lambda _: None)
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_product_urls_keep_only_products_and_the_first_url_per_id() -> None:
    assert product_urls(ENGLISH) == [
        ("HN-659", "https://www.henle.de/en/Waltz-a-minor-op.-34-no.-2/HN-659"),
        ("HN-1", "https://www.henle.de/en/Piano-Sonatas-Volume-I/HN-1"),
    ]


def test_the_english_sitemap_is_found_through_the_index_and_gunzipped() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=INDEX)
        return httpx.Response(200, content=gzip.compress(ENGLISH.encode()))

    with _client(handler) as client:
        xml = fetch_sitemap(client)

    assert "HN-659" in xml
    assert requested == [
        "https://www.henle.de/sitemap.xml",
        f"{SITEMAP_DIR}/x-sitemap-www-henle-de-en-1.xml.gz",
    ]


def test_a_failing_or_foreign_page_is_skipped_and_the_sweep_goes_on() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/BAD"):
            return httpx.Response(404)
        if request.url.path.endswith("/CATEGORY"):
            return httpx.Response(200, text="<html>a category listing</html>")
        return httpx.Response(200, text=PRODUCT)

    urls = [(pid, f"https://www.henle.de/en/x/{pid}") for pid in ("A", "BAD", "CATEGORY", "B")]
    with _client(handler) as client:
        got = [pid for pid, _, _ in iter_products(client, urls)]

    assert got == ["A", "B"]


def test_max_pages_caps_the_pages_requested() -> None:
    urls = [(pid, f"https://www.henle.de/en/x/{pid}") for pid in "ABC"]
    with _client(lambda request: httpx.Response(200, text=PRODUCT)) as client:
        assert len(list(iter_products(client, urls, max_pages=2))) == 2


def test_a_mirrored_page_is_read_without_a_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = PageCache(tmp_path / "pages.db")
    cache.put("https://www.henle.de/en/x/A", PRODUCT)
    slept: list[float] = []
    monkeypatch.setattr(fetch.time, "sleep", slept.append)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request to {request.url}")

    with _client(handler) as client:
        got = list(iter_products(client, [("A", "https://www.henle.de/en/x/A")], cache))

    assert got == [("A", "https://www.henle.de/en/x/A", PRODUCT)]
    assert slept == []


def test_only_a_product_page_is_mirrored(tmp_path: Path) -> None:
    cache = PageCache(tmp_path / "pages.db")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=PRODUCT if request.url.path.endswith("/GOOD") else "<html></html>")

    urls = [(pid, f"https://www.henle.de/en/x/{pid}") for pid in ("GOOD", "EMPTY")]
    with _client(handler) as client:
        list(iter_products(client, urls, cache))

    assert cache.get("https://www.henle.de/en/x/GOOD") is not None
    assert cache.get("https://www.henle.de/en/x/EMPTY") is None
