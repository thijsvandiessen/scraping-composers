"""IMSLP people API access.

The API is awkward: a single GET endpoint whose "query string" is a
slash-separated path inside one parameter, returning a JSON object keyed by
stringified row indices ("0".."999") plus a "metadata" entry that carries the
pagination flag. The retry/backoff lives in the shared :class:`Http` helper.
"""

from __future__ import annotations

from typing import Any

import httpx

from ...http import Http

BASE_URL = "https://imslp.org"

API_URL = BASE_URL + "/imslpscripts/API.ISCR.php"
PAGE_SIZE = 1000  # fixed by the API
REQUEST_DELAY_S = 1.0
RETRIES = 3


def _fetch_page(client: httpx.Client, start: int) -> dict[str, Any]:
    # The API expects its parameters as one slash-separated string; encoding
    # them as separate query params breaks it.
    url = f"{API_URL}?account=worklist/disclaimer=accepted/sort=id/type=1/start={start}/retformat=json"
    data: dict[str, Any] = Http(client, retries=RETRIES).get_json(url, desc=f"page start={start}")
    return data
