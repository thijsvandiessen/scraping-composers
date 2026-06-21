"""New York Philharmonic performance history (Kaggle dataset nyphil/perf-history).

One kagglehub-cached download (see ``data``), parsed into two record types:

1. Per-(role, name) ``person`` records aggregating each composer/conductor/
   soloist's appearances (see ``people``).
2. One work mention per titled work at each concert (see ``performances``),
   carrying its composer and title for the resolution pipeline (with the
   concert's date/location/soloists kept in ``raw``).

``text`` holds the name cleanup both share.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from .. import SourceRecord, SourceWorkMention
from .data import BASE_URL, _load_programs
from .people import ROLES, _aggregate, _record
from .performances import _performances

NAME = "nyphil"

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "NAME", "ROLES", "fetch_records"]


def fetch_records(max_pages: int | None = None) -> Iterator[SourceRecord | SourceWorkMention]:
    """Yield every composer/conductor/soloist in the performance history (one
    aggregated ``person`` record each) followed by every work-performance (one
    work mention each). The whole source is one (kagglehub-cached) download;
    ``max_pages`` is accepted for interface compatibility and ignored."""
    programs = _load_programs()
    log.info("nyphil: %d programs", len(programs))
    people = _aggregate(programs)
    for role in ROLES:
        names = sorted(name for r, name in people if r == role)
        log.info("nyphil %s: %d records", role, len(names))
        for name in names:
            yield _record(role, name, people[(role, name)])

    count = 0
    for record in _performances(programs):
        count += 1
        yield record
    log.info("nyphil performances: %d records", count)
