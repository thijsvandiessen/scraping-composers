"""HTTP access to classicfm.com's composer and artist index pages.

Both pages are single, unpaginated documents (see ``parse``'s docstring for
the markup), so there is no pagination/frontier logic here — just two GETs.
They are the whole source, and they are enumerators, so neither is mirrored.
"""

from __future__ import annotations

from composer_http import SourceSession

BASE_URL = "https://www.classicfm.com"
COMPOSERS_URL = f"{BASE_URL}/composers/"
ARTISTS_URL = f"{BASE_URL}/artists/"
REQUEST_DELAY_S = 0.5
RETRIES = 3


def fetch_page(session: SourceSession, url: str, label: str) -> str:
    return session.get_text(url, label=label, retries=RETRIES)


def fetch_index_pages(session: SourceSession) -> tuple[str, str]:
    """Fetch the composers and artists index pages, in that order."""
    composers_html = fetch_page(session, COMPOSERS_URL, "composers index")
    artists_html = fetch_page(session, ARTISTS_URL, "artists index")
    return composers_html, artists_html
