"""HTTP access to the Concertgebouworkest archive (both views are one request).

The search page is a plain GET; the List view is a multipart POST of the
search form's "List" button with no filters. Each is the whole of what it
lists, so neither is mirrored: a re-run must see the concerts added since.
"""

from __future__ import annotations

from composer_http import SourceSession

BASE_URL = "https://archief.concertgebouworkest.nl"
SEARCH_URL = BASE_URL + "/en/archive/search/"
REQUEST_DELAY_S = 0.5


def _fetch_search_page(session: SourceSession) -> str:
    return session.get_text(SEARCH_URL, label="search page")


def _fetch_list_page(session: SourceSession) -> str:
    # submitting the form's "List" button with no filters returns every concert
    # as multipart/form-data; a plain GET of the list tab does not work
    return session.post_text(SEARCH_URL, label="list view", files={"list": (None, "List")})
