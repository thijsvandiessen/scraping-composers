"""Vienna Philharmonic concert archive (wienerphilharmoniker.at).

The orchestra's complete performance history — every concert it has given since
1842, with programme, conductor, performers and venue. The whole archive is a
dozen plain requests: see :mod:`.fetch` for why no browser is needed,
:mod:`.performances` for the concert blocks and :mod:`.dropdowns` for the filter
vocabularies that name every composer and performer in them.

Two passes, in one sweep of the fragments:

1. one work mention per programme item, carrying its concert's date, venue,
   conductor and performers so the warehouse can rebuild the concert from them;
2. one ``person``/``ensemble`` record per name in the composer and performer
   vocabularies, emitted afterwards because whether a performer conducted is
   only known once every concert has been read.

Soloists carry no discipline: the archive labels only the conductor, and the
instrument and voice types live on the per-concert detail pages, which this
adapter deliberately does not fetch — there is one per concert. Each mention
keeps its concert's URL, so a later pass can add them without re-reading this.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from .. import EntityDocument, RefreshCadence, SourceAdapter, SourceRecord, WorkMentionDocument
from .dropdowns import COMPOSERS, PERFORMERS, WORKS, composer_record, performer_record, vocabularies
from .fetch import BASE_URL, _make_client, fetch_fragments, fetch_landing, total_item_count
from .performances import concerts, mentions

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "WienerPhilAdapter"]


class WienerPhilAdapter(SourceAdapter):
    name = "wienerphil"
    base_url = BASE_URL
    cadence = RefreshCadence.MONTHLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        """Yield every work performed in the archive, then everyone named in it.

        ``max_pages`` caps the number of result fragments fetched (1000 concerts
        each), for test runs; the vocabularies are always complete, so a capped
        run still emits every composer and performer.
        """
        ingested_at = datetime.now(UTC)
        with _make_client() as client:
            landing = fetch_landing(client)
            vocabulary = vocabularies(landing)
            titles = frozenset(vocabulary.get(WORKS, ()))
            conducted: set[str] = set()
            found = works = 0

            for fragment in fetch_fragments(client, landing, max_pages=max_pages):
                for concert in concerts(fragment, titles):
                    found += 1
                    conducted.update(concert.conductors)
                    for mention in mentions(concert):
                        works += 1
                        yield WorkMentionDocument(
                            id=mention.external_id,
                            url=concert.url,
                            source_name=self.name,
                            ingested_at=ingested_at,
                            title=mention.title,
                            composer=mention.composer,
                            raw=mention.raw,
                        )

        expected = total_item_count(landing)
        if max_pages is None and found != expected:
            # the cheapest tripwire for a change in page size or block markup
            log.warning("archive reports %d concerts, parsed %d", expected, found)
        log.info("wienerphil: %d concerts, %d work mentions", found, works)

        people = 0
        for record in self._people(vocabulary, conducted):
            people += 1
            yield EntityDocument(
                id=record.external_id,
                url=record.url,
                source_name=self.name,
                ingested_at=ingested_at,
                name=record.name,
                kind=record.kind,
                raw=record.raw,
                claims=record.claims,
            )
        log.info("wienerphil: %d composer/performer records", people)

    @staticmethod
    def _people(vocabulary: dict[str, list[str]], conducted: set[str]) -> Iterator[SourceRecord]:
        """One record per name in the composer and performer vocabularies.

        The venue vocabulary is deliberately not emitted: a venue is a column on
        the concert, not an entity of its own.
        """
        for name in vocabulary.get(COMPOSERS, ()):
            if name:
                yield composer_record(name)
        for name in vocabulary.get(PERFORMERS, ()):
            if name:
                yield performer_record(name, conducted)
