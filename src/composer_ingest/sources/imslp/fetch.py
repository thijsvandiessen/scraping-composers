"""IMSLP people API access.

The API is awkward: a single GET endpoint whose "query string" is a
slash-separated path inside one parameter, returning a JSON object keyed by
stringified row indices ("0".."999") plus a "metadata" entry that carries the
pagination flag.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

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
