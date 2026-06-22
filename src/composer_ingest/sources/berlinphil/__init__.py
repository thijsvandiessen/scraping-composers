"""Berliner Philharmoniker Digital Concert Hall archive (digitalconcerthall.com).

The Digital Concert Hall is the orchestra's video archive — a high-quality,
already-normalized subset of its concerts (works, composers, conductors,
soloists, periods) spanning several decades, not its complete performance
history. Backed by an unauthenticated JSON API (see ``fetch``), it yields two
kinds of document in one pass over every archived concert:

1. one work-mention document per work performed (``performances``), carrying its
   first composer and title for the resolution pipeline (with conductor/
   soloists, orchestra, period and date kept in ``raw``);
2. one entity (``person`` or ``ensemble``) document per distinct artist
   (``artists``), accumulated across all concerts, with their professions and
   (soloists) the instruments/voices they played.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import httpx

from ...document import Document
from ...scraper import Scraper, SourceConfig
from .artists import _Artist, _artist_records, _collect
from .fetch import BASE_URL, HEADERS, iter_concerts
from .performances import _performances

NAME = "berlinphil"

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "NAME", "SCRAPER"]


def pages(client: httpx.Client, max_pages: int | None = None) -> Iterator[list[dict[str, Any]]]:
    """One payload: every archived concert (``max_pages`` caps how many)."""
    yield list(iter_concerts(client, max_pages=max_pages))


def parse(concerts: list[dict[str, Any]]) -> Iterator[Document]:
    """One work mention per work performed, then one document per distinct
    artist accumulated across all the concerts."""
    registry: dict[str, _Artist] = {}
    works = 0
    for concert in concerts:
        _collect(concert, registry)
        for mention in _performances(concert):
            works += 1
            yield mention
    log.info("berlinphil: %d work-performances, %d artists", works, len(registry))
    yield from _artist_records(registry)


SCRAPER = Scraper(SourceConfig(name=NAME, base_url=BASE_URL, headers=dict(HEADERS)), pages, parse)
