"""Vienna Philharmonic concert archive (wienerphilharmoniker.at).

The orchestra's complete performance history — every concert it has given since
1842, with programme, conductor, performers and venue. Two kinds of page hold
it: see :mod:`.fetch` for both, :mod:`.performances` for the result listing's
concert blocks, :mod:`.details` for a concert's own page, and :mod:`.dropdowns`
for the filter vocabularies that name every composer and performer.

Two passes, in one sweep:

1. one work mention per programme item, carrying its concert's date, venue,
   conductor and performers so the warehouse can rebuild the concert from them;
2. one ``person``/``ensemble`` record per name in the composer and performer
   vocabularies, emitted afterwards because what a performer did — conduct, or
   play the viola — is only known once every concert has been read.

The result listing is a dozen requests for all ten thousand concerts, but it
labels no performer except the conductor, so every concert's own page is fetched
too: one request each, which is the whole cost of this source. That cost is paid
once — the pages go into a :class:`~composer_http.PageCache`, and an archive of
concerts already given does not change (see :mod:`composer_http.pages`).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
from composer_http import PageCache, open_page_cache

from .. import EntityDocument, RefreshCadence, SourceAdapter, SourceRecord, WorkMentionDocument
from .details import detail
from .dropdowns import COMPOSERS, PERFORMERS, WORKS, composer_record, performer_record, vocabularies
from .fetch import BASE_URL, _make_client, fetch_detail, fetch_fragments, fetch_landing, total_item_count
from .performances import Concert, concerts, mentions, merge

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "WienerPhilAdapter"]

#: How often a sweep of ten thousand detail pages says where it is. Hours of
#: silence is not a progress report; this mirrors composer_crawler.progress.
_PROGRESS_EVERY = 100


class WienerPhilAdapter(SourceAdapter):
    name = "wienerphil"
    base_url = BASE_URL
    cadence = RefreshCadence.MONTHLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        """Yield every work performed in the archive, then everyone named in it.

        ``max_pages`` caps the number of concert *detail* pages read — the
        request-per-record half of this source, as in the berlinphil, rco and
        boosey adapters — and so caps the concerts a run covers. The
        vocabularies are always complete, so a capped run still emits every
        composer and performer.
        """
        ingested_at = datetime.now(UTC)
        cache = open_page_cache()
        conducted: set[str] = set()
        disciplines: dict[str, set[str]] = {}
        found = works = plain = 0

        with _make_client() as client:
            landing = fetch_landing(client)
            vocabulary = vocabularies(landing)
            titles = frozenset(vocabulary.get(WORKS, ()))
            expected = total_item_count(landing)
            log.info("wienerphil: %d concerts to read", expected if max_pages is None else max_pages)

            for concert, detailed in self._concerts(client, landing, titles, cache, max_pages):
                found += 1
                plain += not detailed
                conducted.update(concert.conductors)
                for name, discipline in concert.soloists:
                    if discipline:
                        disciplines.setdefault(name, set()).add(discipline)
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
                if found % _PROGRESS_EVERY == 0:
                    log.info("wienerphil: %d concerts read%s", found, _cached(cache))

        if max_pages is None and found != expected:
            # the cheapest tripwire for a change in page size or block markup
            log.warning("archive reports %d concerts, parsed %d", expected, found)
        log.info(
            "wienerphil: %d concerts (%d without a detail page), %d work mentions%s",
            found,
            plain,
            works,
            _cached(cache),
        )

        people = 0
        for record in self._people(vocabulary, conducted, disciplines):
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
    def _concerts(
        client: httpx.Client,
        landing: str,
        titles: frozenset[str],
        cache: PageCache | None,
        max_pages: int | None,
    ) -> Iterator[tuple[Concert, bool]]:
        """Every concert of the archive, each with its own detail page folded in.

        Yields the concert and whether that page was read. It usually was; the
        ones it was not are, in the main, the archive's programme-less entries —
        a cancelled tour date carries no credits block because it has nothing to
        put in one — which produce no work mentions either way. A run's tally of
        them is the tripwire (see :meth:`fetch`), so a markup change shows up as
        the number climbing rather than as one warning per concert.

        Fragments are pulled lazily so a capped run stops without fetching the
        listing pages whose concerts it would never read.
        """
        read = 0
        for fragment in fetch_fragments(client, landing):
            for concert in concerts(fragment, titles):
                if max_pages is not None and read >= max_pages:
                    return
                read += 1
                page = fetch_detail(client, concert.url, cache)
                found = detail(page) if page is not None else None
                if found is None:
                    log.debug("concert %s: no detail page read", concert.concert_id)
                    yield concert, False
                else:
                    yield merge(concert, found), True

    @staticmethod
    def _people(
        vocabulary: dict[str, list[str]],
        conducted: set[str],
        disciplines: dict[str, set[str]],
    ) -> Iterator[SourceRecord]:
        """One record per name in the composer and performer vocabularies.

        The venue vocabulary is deliberately not emitted: a venue is a column on
        the concert, not an entity of its own.
        """
        for name in vocabulary.get(COMPOSERS, ()):
            if name:
                yield composer_record(name)
        for name in vocabulary.get(PERFORMERS, ()):
            if name:
                yield performer_record(name, conducted, disciplines)


def _cached(cache: PageCache | None) -> str:
    """The mirror's tally, as a clause to hang off a progress line."""
    return f" ({cache.summary()})" if cache is not None else ""
