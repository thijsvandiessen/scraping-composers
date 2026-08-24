"""Composer and artist name lists from classicfm.com.

classicfm.com publishes two static, unpaginated index pages — /composers/ and
/artists/ — each an A-Z list of names linking to a bio page. Both pages share
identical markup (see ``parse``), so one adapter covers both; only the source
page tells the two apart, since the site does not otherwise distinguish
composers from performers/conductors/ensembles.

Composer-page entries get a ``has_profession=composer`` claim, mirroring
classicalmusiconline's index. Artist-page entries get no claims: the
/artists/ list mixes soloists, conductors and ensembles (e.g. "LSO", "2Cellos")
that can't be told apart from the name text alone, so — like imslp's people
list — they come through as bare names.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from .. import EntityDocument, RefreshCadence, SourceAdapter, SourceClaim
from .fetch import ARTISTS_URL, BASE_URL, COMPOSERS_URL, fetch_index_pages
from .parse import parse_entries

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "ClassicFmAdapter"]

_COMPOSER_CLAIMS: tuple[SourceClaim, ...] = (SourceClaim("has_profession", "profession", "composer"),)
_ARTIST_CLAIMS: tuple[SourceClaim, ...] = ()


class ClassicFmAdapter(SourceAdapter):
    name = "classicfm"
    base_url = BASE_URL
    cadence = RefreshCadence.MONTHLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument]:
        """Yield one EntityDocument per name on the composers and artists indexes.

        ``max_pages`` caps the total number of entries yielded — there is no
        real pagination here, just two static pages — for test runs.
        """
        composers_html, artists_html = fetch_index_pages()
        ingested_at = datetime.now(UTC)

        count = 0
        for source_url, page_html, claims in (
            (COMPOSERS_URL, composers_html, _COMPOSER_CLAIMS),
            (ARTISTS_URL, artists_html, _ARTIST_CLAIMS),
        ):
            entries = parse_entries(page_html)
            log.info("classicfm %s: %d names", source_url, len(entries))
            for entry in entries:
                if max_pages is not None and count >= max_pages:
                    return
                yield EntityDocument(
                    id=entry.path,
                    url=BASE_URL + entry.path,
                    source_name=self.name,
                    ingested_at=ingested_at,
                    name=entry.name,
                    kind="person",
                    claims=claims,
                )
                count += 1
