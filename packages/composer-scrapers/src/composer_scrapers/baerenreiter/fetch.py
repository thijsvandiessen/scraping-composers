"""HTTP access to the Bärenreiter shop API, mirrored product by product.

baerenreiter.com is a client-rendered single-page app: a product page's HTML is
the same 10KB shell for every product, and the metadata arrives from a JSON API
the page calls (the reason the earlier generic crawl of this site stored 34,719
empty pages). That API needs no authentication, so it is read directly:

- ``/api/sitemap/products_en.xml`` lists every product id — the inventory;
- ``/api/bv/product/{id}?lang=en`` is one product, every field the page shows.

The shop's search (``POST /api/bv/product/query``) returns the same records a
thousand at a time, but it is no inventory: it answers HTTP 500 past the 10,000th
hit (an Elasticsearch result window) and omits products hidden from search. So
the sitemap drives, one request per product, through
:class:`~composer_http.PageCache`: the first sweep is paid once, an interrupted
run resumes, and the raw JSON stays on disk for re-parsing without the network.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterable, Iterator
from typing import Any

import httpx
from composer_http import PageCache, get_text, new_client

log = logging.getLogger(__name__)

BASE_URL = "https://www.baerenreiter.com"
SITEMAP_URL = f"{BASE_URL}/api/sitemap/products_en.xml"

#: Between uncached requests. robots.txt allows everything and sets no
#: crawl-delay; this is the politeness rate the other catalogues here use.
REQUEST_DELAY_S = 0.5

#: The sitemap's ``<loc>`` is namespace-prefixed (``<ns4:loc>``), so match the
#: product path rather than the element.
_PRODUCT_LOC = re.compile(r"/en/product/([^<\s/?#]+)\s*<")


def product_url(product_id: str) -> str:
    """The product's page on the site, which is what a document links to."""
    return f"{BASE_URL}/en/product/{product_id}"


def api_url(product_id: str) -> str:
    return f"{BASE_URL}/api/bv/product/{product_id}?lang=en"


def make_client() -> httpx.Client:
    """A client that follows redirects, identified by the project's contact UA."""
    client = new_client()
    client.follow_redirects = True
    return client


def product_ids(sitemap_xml: str) -> list[str]:
    """Every product id the sitemap lists, deduplicated, in file order."""
    return list(dict.fromkeys(_PRODUCT_LOC.findall(sitemap_xml)))


def fetch_sitemap(client: httpx.Client) -> str:
    return get_text(client, SITEMAP_URL, label="sitemap")


def fetch_product(
    client: httpx.Client, product_id: str, cache: PageCache | None = None
) -> dict[str, Any] | None:
    """One product's JSON, from the mirror when it holds it.

    Returns ``None`` rather than raising when the product cannot be fetched or
    read: a sweep of seventeen thousand products must not be lost to one of them.
    Only a readable answer is mirrored, so a failure is retried on the next run.
    """
    url = api_url(product_id)
    body = cache.get(url) if cache is not None else None
    fetched = body is None
    if body is None:
        try:
            body = get_text(client, url, label=product_id)
        except httpx.HTTPError as exc:
            log.warning("baerenreiter: skipping %s: %s", product_id, exc)
            time.sleep(REQUEST_DELAY_S)
            return None
    try:
        payload = json.loads(body)
    except ValueError:
        log.warning("baerenreiter: %s did not answer JSON", product_id)
        payload = None
    if fetched:
        if cache is not None and isinstance(payload, dict):
            cache.put(url, body)
        time.sleep(REQUEST_DELAY_S)
    return payload if isinstance(payload, dict) else None


def iter_products(
    client: httpx.Client,
    ids: Iterable[str],
    cache: PageCache | None = None,
    max_pages: int | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """``(product_id, payload)`` for each of *ids* that answers; ``max_pages``
    caps how many are requested."""
    for requested, product_id in enumerate(ids):
        if max_pages is not None and requested >= max_pages:
            return
        if (payload := fetch_product(client, product_id, cache)) is not None:
            yield product_id, payload
