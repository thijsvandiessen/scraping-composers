"""HTTP access to classicfm.com's composer and artist index pages.

Both pages are single, unpaginated documents (see ``parse``'s docstring for
the markup), so there is no pagination/frontier logic here — just two GETs
with a polite delay between them.
"""

from __future__ import annotations

import time

import httpx
from composer_http import get_text, user_agent

BASE_URL = "https://www.classicfm.com"
COMPOSERS_URL = f"{BASE_URL}/composers/"
ARTISTS_URL = f"{BASE_URL}/artists/"
REQUEST_DELAY_S = 0.5
RETRIES = 3


def _make_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": user_agent()}, timeout=30, follow_redirects=True)


def fetch_page(client: httpx.Client, url: str, label: str) -> str:
    return get_text(client, url, label=label, retries=RETRIES)


def fetch_index_pages() -> tuple[str, str]:
    """Fetch the composers and artists index pages, in that order."""
    with _make_client() as client:
        composers_html = fetch_page(client, COMPOSERS_URL, "composers index")
        time.sleep(REQUEST_DELAY_S)
        artists_html = fetch_page(client, ARTISTS_URL, "artists index")
    return composers_html, artists_html
