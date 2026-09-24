"""HTTP access to henle.de, mirrored product page by product page.

henle.de is a Shopware shop whose product pages are rendered on the server, so
unlike Bärenreiter's there is no API to go round: the page *is* the record. Its
``robots.txt`` disallows ``/api/`` and ``/search`` but allows the product pages,
and the sitemap lists every one of them:

- ``/sitemap.xml`` is an index naming one gzipped sitemap per language;
- the English one lists each edition at ``/en/<slug>/HN-<n>`` — the inventory.

So the sitemap drives, one request per product, through
:class:`~composer_http.PageCache`. The pages are large (~800KB, mostly the shop's
navigation repeated on every page) but gzip to ~55KB, so the mirror of the whole
catalogue stays around 120MB.
"""

from __future__ import annotations

import gzip
import logging
import re
import time
from collections.abc import Iterable, Iterator

import httpx
from composer_http import PageCache, call_with_retries, get_text, new_client

log = logging.getLogger(__name__)

BASE_URL = "https://www.henle.de"
SITEMAP_INDEX_URL = f"{BASE_URL}/sitemap.xml"

#: Between uncached requests: the politeness rate the old crawl recipe for this
#: site used. robots.txt sets no crawl-delay.
REQUEST_DELAY_S = 1.0

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
#: The English sitemap's file name ends ``-sitemap-www-henle-de-en-<n>.xml.gz``.
_ENGLISH_SITEMAP = re.compile(r"-en-\d+\.xml(?:\.gz)?$")
#: ``https://www.henle.de/en/Waltz-a-minor-op.-34-no.-2/HN-659``: the slug is
#: decorative, the HN number is the id.
_PRODUCT_URL = re.compile(r"^https://www\.henle\.de/en/[^\s/]+/HN-(\d+)$")
#: Present on every product page and on nothing else; a page without it is a
#: redirect to a category or an error page served with 200.
PRODUCT_MARKER = "product-detail-name"


def make_client() -> httpx.Client:
    """A client that follows redirects, identified by the project's contact UA."""
    client = new_client()
    client.follow_redirects = True
    return client


def english_sitemaps(index_xml: str) -> list[str]:
    """The English sitemaps the index names."""
    return [loc for loc in _LOC.findall(index_xml) if _ENGLISH_SITEMAP.search(loc)]


def product_urls(sitemap_xml: str) -> list[tuple[str, str]]:
    """``(product_id, url)`` for every product the sitemap lists, deduplicated,
    in file order. The id is ``HN-<n>``, as the URL writes it."""
    found: dict[str, str] = {}
    for loc in _LOC.findall(sitemap_xml):
        if (match := _PRODUCT_URL.match(loc)) is not None:
            found.setdefault(f"HN-{match.group(1)}", loc)
    return list(found.items())


def _get_bytes(client: httpx.Client, url: str) -> bytes:
    def do() -> bytes:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content

    return call_with_retries(do, label="sitemap")


def _decompress(body: bytes) -> str:
    """A sitemap body, gunzipped when it is still compressed (the ``.gz`` file is
    served as ``application/gzip``, so httpx does not undo it)."""
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    return body.decode("utf-8")


def fetch_sitemap(client: httpx.Client) -> str:
    """The English product sitemaps, concatenated."""
    index = get_text(client, SITEMAP_INDEX_URL, label="sitemap index")
    return "\n".join(_decompress(_get_bytes(client, url)) for url in english_sitemaps(index))


def fetch_product(client: httpx.Client, url: str, cache: PageCache | None = None) -> str | None:
    """One product page, from the mirror when it holds it.

    Returns ``None`` rather than raising when the page cannot be fetched or is
    not a product page: a sweep of two thousand pages must not be lost to one.
    Only a product page is mirrored, so a failure is retried on the next run.
    """
    body = cache.get(url) if cache is not None else None
    if body is not None:
        return body
    try:
        body = get_text(client, url, label=url)
    except httpx.HTTPError as exc:
        log.warning("henle: skipping %s: %s", url, exc)
        time.sleep(REQUEST_DELAY_S)
        return None
    time.sleep(REQUEST_DELAY_S)
    if PRODUCT_MARKER not in body:
        log.warning("henle: %s is not a product page", url)
        return None
    if cache is not None:
        cache.put(url, body)
    return body


def iter_products(
    client: httpx.Client,
    products: Iterable[tuple[str, str]],
    cache: PageCache | None = None,
    max_pages: int | None = None,
) -> Iterator[tuple[str, str, str]]:
    """``(product_id, url, html)`` for each product that answers; ``max_pages``
    caps how many are requested."""
    for requested, (product_id, url) in enumerate(products):
        if max_pages is not None and requested >= max_pages:
            return
        if (body := fetch_product(client, url, cache)) is not None:
            yield product_id, url, body
