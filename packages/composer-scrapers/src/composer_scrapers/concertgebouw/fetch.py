"""HTTP access to the Concertgebouworkest archive (both views are one request).

The search page is a plain GET; the List view is a multipart POST of the
search form's "List" button with no filters.
"""

from __future__ import annotations

import httpx
from composer_http import call_with_retries, new_client

BASE_URL = "https://archief.concertgebouworkest.nl"
SEARCH_URL = BASE_URL + "/en/archive/search/"
REQUEST_DELAY_S = 0.5


def _make_client() -> httpx.Client:
    return new_client()


def _fetch(client: httpx.Client, label: str, **request: object) -> str:
    # Not composer_http.get_text: the List view is a multipart POST, so the whole
    # request is passed through rather than just a URL.
    def do() -> str:
        resp = client.request(**request)  # pyright: ignore[reportArgumentType]
        resp.raise_for_status()
        return resp.text

    return call_with_retries(do, label=label)


def _fetch_search_page(client: httpx.Client) -> str:
    return _fetch(client, "search page", method="GET", url=SEARCH_URL)


def _fetch_list_page(client: httpx.Client) -> str:
    # submitting the form's "List" button with no filters returns every concert
    # as multipart/form-data; a plain GET of the list tab does not work
    return _fetch(client, "list view", method="POST", url=SEARCH_URL, files={"list": (None, "List")})
