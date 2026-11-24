"""Wikidata SPARQL client.

Fetches every item with occupation "composer" (Q36834) or "conductor" (Q158852)
from the Wikidata Query Service (``query.OCCUPATIONS``). ``query`` holds the
SPARQL access (id-list queries + per-batch detail and metrics queries);
``parse`` folds the result rows into one SourceRecord per person and formats
birth/death dates to their recorded precision. ``works``
is a second adapter over the same service: works with a recorded premiere.
``places`` is a third: the places composers point at, with type, hierarchy and
coordinates. ``genres`` is a fourth: the genres and movements those composers
are tagged with, and their ``subclass of`` hierarchy.
``ensembles`` a fifth: orchestras and choirs, the anchors name-keyed
ensembles are folded onto. ``catalogue`` a sixth: works with a catalogue
number, carrying their MusicBrainz ids, ISWCs and parent works for the
warehouse's work identity. ``instrumentation`` a seventh: works with a stated
instrumentation (P870), as instrumentation claims and parts with their counts.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from composer_http import SourceSession

from .. import EntityDocument, HttpSourceAdapter, RefreshCadence
from .catalogue import WikidataCatalogueAdapter
from .ensembles import WikidataEnsemblesAdapter
from .genres import WikidataGenresAdapter
from .instrumentation import WikidataInstrumentationAdapter
from .parse import BASE_URL, records_from_rows
from .places import WikidataPlacesAdapter
from .query import PAGE_SIZE, REQUEST_DELAY_S, TIMEOUT_S, fetch_metrics, fetch_page, fetch_population
from .works import WikidataWorksAdapter

log = logging.getLogger(__name__)

__all__ = [
    "BASE_URL",
    "WikidataAdapter",
    "WikidataCatalogueAdapter",
    "WikidataEnsemblesAdapter",
    "WikidataGenresAdapter",
    "WikidataInstrumentationAdapter",
    "WikidataPlacesAdapter",
    "WikidataWorksAdapter",
]


class WikidataAdapter(HttpSourceAdapter[EntityDocument]):
    name = "wikidata"
    base_url = BASE_URL
    cadence = RefreshCadence.YEARLY
    request_delay_s = REQUEST_DELAY_S
    timeout_s = TIMEOUT_S

    def scrape(self, session: SourceSession, max_pages: int | None = None) -> Iterator[EntityDocument]:
        """Yield every composer and conductor on Wikidata (``query.OCCUPATIONS``).

        The full id list is fetched first, then details are fetched in
        PAGE_SIZE batches bound with VALUES -- see the ``query`` module for why
        the pages are driven from a client-side list rather than a server-side
        cursor. ``max_pages`` caps the number of batches, for test runs; ids
        are numerically ordered, so a capped run still covers the best-known
        composers."""
        population = fetch_population(session)
        qids = list(population)
        batches = [qids[i : i + PAGE_SIZE] for i in range(0, len(qids), PAGE_SIZE)]
        if max_pages is not None and max_pages < len(batches):
            log.info("stopping after max_pages=%d", max_pages)
            batches = batches[:max_pages]
        log.info("wikidata: %d persons in %d pages", len(qids), len(batches))

        for page, batch in enumerate(batches, start=1):
            rows = fetch_page(session, batch)
            metrics = fetch_metrics(session, batch)
            records = records_from_rows(rows, metrics, population)
            ingested_at = datetime.now(UTC)
            log.info("wikidata page %d/%d: %d persons", page, len(batches), len(records))
            for record in records:
                yield EntityDocument(
                    id=record.external_id,
                    url=record.url,
                    source_name=self.name,
                    ingested_at=ingested_at,
                    name=record.name,
                    kind=record.kind,
                    raw=record.raw,
                    claims=record.claims,
                    language=record.language,
                )
