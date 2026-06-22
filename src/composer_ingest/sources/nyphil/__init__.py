"""New York Philharmonic performance history (Kaggle dataset nyphil/perf-history).

One kagglehub-cached download (see ``data``), parsed into two document types:

1. Per-(role, name) entity (``person``) documents aggregating each composer/
   conductor/soloist's appearances (see ``people``).
2. One work-mention document per titled work at each concert (see
   ``performances``), carrying its composer and title for the resolution
   pipeline (with the concert's date/location/soloists kept in ``raw``).

``text`` holds the name cleanup both share.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import httpx

from ...document import Document
from ...scraper import Scraper, SourceConfig
from .data import BASE_URL, _load_programs
from .people import ROLES, _aggregate, _record
from .performances import _performances

NAME = "nyphil"

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "NAME", "ROLES", "SCRAPER"]


def pages(client: httpx.Client, max_pages: int | None = None) -> Iterator[list[dict[str, Any]]]:
    """The whole source is one (kagglehub-cached) download; ``client`` and
    ``max_pages`` are accepted for interface compatibility and ignored."""
    programs = _load_programs()
    log.info("nyphil: %d programs", len(programs))
    yield programs


def parse(programs: list[dict[str, Any]]) -> Iterator[Document]:
    """Aggregated person documents, then one work mention per work-performance."""
    people = _aggregate(programs)
    for role in ROLES:
        names = sorted(name for r, name in people if r == role)
        log.info("nyphil %s: %d records", role, len(names))
        for name in names:
            yield _record(role, name, people[(role, name)])

    count = 0
    for mention in _performances(programs):
        count += 1
        yield mention
    log.info("nyphil performances: %d records", count)


SCRAPER = Scraper(SourceConfig(name=NAME, base_url=BASE_URL), pages, parse)
