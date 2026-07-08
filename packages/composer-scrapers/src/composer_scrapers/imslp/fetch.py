"""IMSLP people API access.

The API is awkward: a single GET endpoint whose "query string" is a
slash-separated path inside one parameter, returning a JSON object keyed by
stringified row indices ("0".."999") plus a "metadata" entry that carries the
pagination flag.
"""

from __future__ import annotations

from typing import Any

import httpx

from .._http import call_with_retries

BASE_URL = "https://imslp.org"

API_URL = BASE_URL + "/imslpscripts/API.ISCR.php"
PAGE_SIZE = 1000  # fixed by the API
REQUEST_DELAY_S = 1.0
RETRIES = 3


def _fetch_page(client: httpx.Client, start: int) -> dict[str, Any]:
    # The API expects its parameters as one slash-separated string; encoding
    # them as separate query params breaks it.
    url = f"{API_URL}?account=worklist/disclaimer=accepted/sort=id/type=1/start={start}/retformat=json"

    def do() -> dict[str, Any]:
        resp = client.get(url)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    return call_with_retries(
        do, label=f"page start={start}", retries=RETRIES, retry_on=(httpx.HTTPError, ValueError)
    )
