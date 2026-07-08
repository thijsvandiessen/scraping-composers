"""HTTP access to the Berliner Philharmoniker Digital Concert Hall API.

The public website (digitalconcerthall.com) is a JavaScript app, but it is
backed by an unauthenticated JSON API at ``api.digitalconcerthall.com/v2``.
``v2/concerts`` lists every concert in the archive in one response; each
``v2/concert/{id}`` carries the full programme (works, composers, conductors,
soloists) embedded. ``Accept-Language: en`` selects the English titles/labels
that match the ``/en/`` website.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx

from .._http import call_with_retries, user_agent

BASE_URL = "https://www.digitalconcerthall.com"
API_URL = "https://api.digitalconcerthall.com/v2"
REQUEST_DELAY_S = 0.5
RETRIES = 3

log = logging.getLogger(__name__)


def _fetch_json(client: httpx.Client, label: str, path: str) -> dict[str, Any]:
    def do() -> dict[str, Any]:
        resp = client.get(f"{API_URL}/{path}")
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    return call_with_retries(do, label=label, retries=RETRIES, retry_on=(httpx.HTTPError, ValueError))


def _concert_ids(client: httpx.Client) -> list[str]:
    """Every concert id in the archive, newest first (the order the API gives)."""
    data = _fetch_json(client, "concert list", "concerts")
    concerts = data.get("_links", {}).get("concert", [])
    return [c["id"] for c in concerts if c.get("id")]


def iter_concerts(max_pages: int | None = None) -> Iterator[dict[str, Any]]:
    """Yield the full detail payload of each archived concert.

    ``max_pages`` caps the number of concert detail pages fetched (one request
    each) for quick test runs; ``None`` fetches them all.
    """
    headers = {"User-Agent": user_agent(), "Accept-Language": "en"}
    with httpx.Client(headers=headers, timeout=30) as client:
        ids = _concert_ids(client)
        if max_pages is not None:
            ids = ids[:max_pages]
        log.info("berlinphil: %d concerts to fetch", len(ids))
        for concert_id in ids:
            yield _fetch_json(client, f"concert {concert_id}", f"concert/{concert_id}")
            time.sleep(REQUEST_DELAY_S)
