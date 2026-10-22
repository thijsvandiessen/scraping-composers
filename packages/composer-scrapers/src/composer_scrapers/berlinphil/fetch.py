"""HTTP access to the Berliner Philharmoniker Digital Concert Hall API.

The public website (digitalconcerthall.com) is a JavaScript app, but it is
backed by an unauthenticated JSON API at ``api.digitalconcerthall.com/v2``.
``v2/concerts`` lists every concert in the archive in one response; each
``v2/concert/{id}`` carries the full programme (works, composers, conductors,
soloists) embedded. ``Accept-Language: en`` (set on the adapter) selects the
English titles/labels that match the ``/en/`` website.

The concert list is the enumerator and stays live; each concert's detail is
mirrored — the archive is a record of concerts already given.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from composer_http import SourceSession

BASE_URL = "https://www.digitalconcerthall.com"
API_URL = "https://api.digitalconcerthall.com/v2"
REQUEST_DELAY_S = 0.5

log = logging.getLogger(__name__)


def _fetch_json(session: SourceSession, label: str, path: str, *, mirror: bool = False) -> dict[str, Any]:
    return session.get_json(f"{API_URL}/{path}", label=label, mirror=mirror)


def _concert_ids(session: SourceSession) -> list[str]:
    """Every concert id in the archive, newest first (the order the API gives)."""
    data = _fetch_json(session, "concert list", "concerts")
    concerts = data.get("_links", {}).get("concert", [])
    return [c["id"] for c in concerts if c.get("id")]


def iter_concerts(session: SourceSession, max_pages: int | None = None) -> Iterator[dict[str, Any]]:
    """Yield the full detail payload of each archived concert.

    ``max_pages`` caps the number of concert detail pages fetched (one request
    each) for quick test runs; ``None`` fetches them all.
    """
    ids = _concert_ids(session)
    if max_pages is not None:
        ids = ids[:max_pages]
    log.info("berlinphil: %d concerts to fetch", len(ids))
    for concert_id in ids:
        yield _fetch_json(session, f"concert {concert_id}", f"concert/{concert_id}", mirror=True)
