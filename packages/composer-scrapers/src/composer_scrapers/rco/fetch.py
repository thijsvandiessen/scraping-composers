"""HTTP access to the Royal Concertgebouw Orchestra website.

Two data feeds:
1. Calendar: paginated HTML listing (slug discovery) + JSON detail per concert.
2. Conductors: single JSON listing of all conductors with biographies.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator
from typing import Any

import httpx

BASE_URL = "https://www.concertgebouworkest.nl"
REQUEST_DELAY_S = 0.5
RETRIES = 3
PAGE_SIZE = 50

# Slugs end with an ISO date: e.g. vikingur-olafsson-...-2026-08-26
_SLUG_RE = re.compile(r'href="/en/calendar/([a-z0-9][a-z0-9-]*-\d{4}-\d{2}-\d{2})/"')

log = logging.getLogger(__name__)


def _make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": "composer-ingest/0.1 (research; thijsvandiessen@gmail.com)"},
        timeout=30,
    )


def _get_text(client: httpx.Client, url: str, label: str) -> str:
    for attempt in range(1, RETRIES + 1):
        try:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPError as exc:
            if attempt == RETRIES:
                raise
            wait = 2**attempt
            log.warning("%s fetch failed (%s), retrying in %ds", label, exc, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


def _get_json(client: httpx.Client, url: str, label: str) -> dict[str, Any]:
    return json.loads(_get_text(client, url, label))


def page_slugs(html: str) -> list[str]:
    """Extract concert slugs from one calendar HTML page."""
    return _SLUG_RE.findall(html)


def iter_concerts(max_pages: int | None = None) -> Iterator[dict[str, Any]]:
    """Yield concert detail dicts for every event on the calendar.

    Paginates the HTML calendar to discover slugs, then fetches the JSON detail
    endpoint for each. ``max_pages`` caps the number of concert detail fetches
    (not listing pages), for use in test runs.
    """
    seen: set[str] = set()
    count = 0
    with _make_client() as client:
        offset = 0
        while True:
            url = f"{BASE_URL}/en/calendar/?limit={PAGE_SIZE}&locale=en&offset={offset}"
            html = _get_text(client, url, f"calendar offset={offset}")
            new_slugs = [s for s in page_slugs(html) if s not in seen]
            if not new_slugs:
                break
            for slug in new_slugs:
                seen.add(slug)
                detail_url = f"{BASE_URL}/api/pages/calendar/{slug}/?locale=en&dialog=1"
                try:
                    concert = _get_json(client, detail_url, f"concert {slug}")
                except httpx.HTTPError as exc:
                    log.warning("skipping concert %s: %s", slug, exc)
                    continue
                yield concert
                count += 1
                if max_pages is not None and count >= max_pages:
                    return
                time.sleep(REQUEST_DELAY_S)
            offset += PAGE_SIZE
            time.sleep(REQUEST_DELAY_S)


def fetch_conductors() -> dict[str, Any]:
    """Fetch the conductors overview page with all conductor profiles."""
    url = f"{BASE_URL}/api/pages/orchestra/conductors/?locale=en"
    with _make_client() as client:
        return _get_json(client, url, "conductors page")
