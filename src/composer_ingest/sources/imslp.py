"""IMSLP people API client.

The API is awkward: a single GET endpoint whose "query string" is a
slash-separated path inside one parameter, returning a JSON object keyed by
stringified row indices ("0".."999") plus a "metadata" entry that carries the
pagination flag. People (type=1) come back with their name embedded in a
MediaWiki category title ("Category:Beethoven, Ludwig van") and an empty
``intvals``. This module hides all of that and yields clean SourceRecords.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx

from . import SourceRecord

NAME = "imslp"
BASE_URL = "https://imslp.org"

API_URL = BASE_URL + "/imslpscripts/API.ISCR.php"
PAGE_SIZE = 1000  # fixed by the API
REQUEST_DELAY_S = 1.0
RETRIES = 3

log = logging.getLogger(__name__)


def _fetch_page(client: httpx.Client, start: int) -> dict[str, Any]:
    # The API expects its parameters as one slash-separated string; encoding
    # them as separate query params breaks it.
    url = f"{API_URL}?account=worklist/disclaimer=accepted/sort=id/type=1/start={start}/retformat=json"
    for attempt in range(1, RETRIES + 1):
        try:
            resp = client.get(url)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data
        except (httpx.HTTPError, ValueError) as exc:
            if attempt == RETRIES:
                raise
            wait = 2**attempt
            log.warning("page start=%d failed (%s), retrying in %ds", start, exc, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


def fetch_records(max_pages: int | None = None) -> Iterator[SourceRecord]:
    """Yield every person listed on IMSLP, paging until the API is exhausted."""
    start = 0
    pages = 0
    with httpx.Client(
        headers={"User-Agent": "composer-ingest/0.1 (research; thijsvandiessen@gmail.com)"},
        timeout=30,
    ) as client:
        while True:
            data = _fetch_page(client, start)
            meta = data.pop("metadata", {})
            pages += 1
            log.info("imslp page %d: %d records (start=%d)", pages, len(data), start)

            # Rows are keyed "0", "1", ... — sort numerically to keep API order.
            for key in sorted(data, key=int):
                row = data[key]
                category_id = row.get("id", "")
                name = category_id.removeprefix("Category:").strip()
                if not name:
                    continue
                yield SourceRecord(
                    external_id=category_id,
                    name=name,
                    url=row.get("permlink"),
                    raw=row,
                )

            if not meta.get("moreresultsavailable"):
                break
            if max_pages is not None and pages >= max_pages:
                log.info("stopping after max_pages=%d", max_pages)
                break
            start += PAGE_SIZE
            time.sleep(REQUEST_DELAY_S)
