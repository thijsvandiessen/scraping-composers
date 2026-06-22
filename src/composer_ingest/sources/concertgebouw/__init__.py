"""Concertgebouworkest concert archive (archief.concertgebouworkest.nl).

Two views of the archive, both fetched in one request each (see ``fetch`` for
the HTTP, ``dropdowns`` for the per-person filter lists, and ``performances``
for the List view of every work performed):

1. The search form's filter dropdowns yield one entity (``person``) document
   each, with the profession and (composers) birth/death years and (soloists)
   discipline.
2. The "List" view yields one work-mention document per work-performance, with
   its composer/conductor/soloists/date/city kept in ``raw``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import httpx

from ...document import Document
from ...scraper import Scraper, SourceConfig
from .dropdowns import SELECTS, _options, _record
from .fetch import BASE_URL, _fetch_list_page, _fetch_search_page
from .performances import _performances

NAME = "concertgebouw"

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "NAME", "SCRAPER"]

# one tagged view of the archive: ("search", html) or ("list", html)
_View = tuple[str, str]


def pages(client: httpx.Client, max_pages: int | None = None) -> Iterator[_View]:
    """The whole source is two fetches; ``max_pages`` is accepted for interface
    compatibility and ignored."""
    yield "search", _fetch_search_page(client)
    yield "list", _fetch_list_page(client)


def parse(view: _View) -> Iterator[Document]:
    """Entity documents from the search-page dropdowns, work mentions from the
    List view."""
    kind, page = view
    if kind == "search":
        for select_id, profession in SELECTS:
            count = 0
            for value, label in _options(page, select_id):
                doc = _record(select_id, profession, value, label)
                if doc is not None:
                    count += 1
                    yield doc
            log.info("concertgebouw %s: %d records", select_id, count)
    else:
        count = 0
        for mention in _performances(page):
            count += 1
            yield mention
        log.info("concertgebouw performances: %d records", count)


SCRAPER = Scraper(SourceConfig(name=NAME, base_url=BASE_URL), pages, parse)
