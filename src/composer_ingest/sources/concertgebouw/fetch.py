"""HTTP access to the Concertgebouworkest archive (both views are one request).

The search page is a plain GET; the List view is a multipart POST of the
search form's "List" button with no filters. The retry/backoff lives in the
shared :class:`Http` helper.
"""

from __future__ import annotations

from typing import Any

import httpx

from ...http import Http

BASE_URL = "https://archief.concertgebouworkest.nl"
SEARCH_URL = BASE_URL + "/en/archive/search/"
RETRIES = 3


def _fetch(client: httpx.Client, label: str, **request: Any) -> str:
    return Http(client, retries=RETRIES).request_text(desc=label, **request)


def _fetch_search_page(client: httpx.Client) -> str:
    return _fetch(client, "search page", method="GET", url=SEARCH_URL)


def _fetch_list_page(client: httpx.Client) -> str:
    # submitting the form's "List" button with no filters returns every concert
    # as multipart/form-data; a plain GET of the list tab does not work
    return _fetch(client, "list view", method="POST", url=SEARCH_URL, files={"list": (None, "List")})
