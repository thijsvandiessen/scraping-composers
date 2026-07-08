"""HTTP access to the Concertgebouworkest archive (both views are one request).

The search page is a plain GET; the List view is a multipart POST of the
search form's "List" button with no filters.
"""

from __future__ import annotations

import httpx

from .._http import call_with_retries, user_agent

BASE_URL = "https://archief.concertgebouworkest.nl"
SEARCH_URL = BASE_URL + "/en/archive/search/"
RETRIES = 3


def _fetch(label: str, **request: object) -> str:
    with httpx.Client(headers={"User-Agent": user_agent()}, timeout=30) as client:

        def do() -> str:
            resp = client.request(**request)  # pyright: ignore[reportArgumentType]
            resp.raise_for_status()
            return resp.text

        return call_with_retries(do, label=label, retries=RETRIES)


def _fetch_search_page() -> str:
    return _fetch("search page", method="GET", url=SEARCH_URL)


def _fetch_list_page() -> str:
    # submitting the form's "List" button with no filters returns every concert
    # as multipart/form-data; a plain GET of the list tab does not work
    return _fetch("list view", method="POST", url=SEARCH_URL, files={"list": (None, "List")})
