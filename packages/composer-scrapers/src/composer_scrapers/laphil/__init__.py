"""Los Angeles Philharmonic — composers, read from the concert programmes.

This source used to be scraped by the generic LLM crawler, and the result did
not survive contact with the data: in its last snapshot every one of the 16,298
work mentions had ``composer: null``, the "works" were marketing titles
("Mozart Under the Stars", "Beethoven Symphonies"), and each person's profession
was a guess. None of that was necessary. laphil.com states the composer of every
programmed work in server-rendered markup, as a link to that composer's own
page:

    <a href="/people/manuel-de-falla" class="program-item__composer …">FALLA</a>

So this adapter reads the site instead of interpreting it. **A person becomes a
composer here only because an event programme credited them as one** — never
because a name looked like a composer's.

Getting to those programmes is the whole difficulty. ``sitemap.xml`` is capped
at 1000 URLs per section and there is no paginated variant, the calendar renders
client-side, and ``/search`` returns nothing server-side; meanwhile a person page
carries no usable composer signal of its own (``jobTitle`` was absent on 38 of a
random 40). What the site does have is a dense graph — an event lists its
artists, a person lists their events — so the sitemap seeds a walk over the
entire ``/events/`` + ``/people/`` subgraph rather than bounding it.

Every page fetched is mirrored by :class:`~composer_http.PageCache` (see
:mod:`.fetch`). Today only the composer credits are read, but the raw HTML stays
on disk, and :mod:`.events` and :mod:`.people` already parse a good deal more
than this pass emits — programmed works and their durations, premiere and
commission notes, performer credits and roles, biographies, images. A later
concerts or performers pass re-parses the mirror without going back to the
network.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, datetime

import httpx
from composer_http import PageCache, open_page_cache
from composer_schema import resolve_entity_kind

from .. import EntityDocument, RefreshCadence, SourceAdapter, SourceClaim
from .events import composer_slugs
from .fetch import fetch_page, fetch_sitemap, make_client
from .people import PersonPage, parse_person
from .sitemap import seed_urls
from .urls import BASE_URL, links, person_url, section, slug

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "LaPhilAdapter"]


class LaPhilAdapter(SourceAdapter):
    """Every composer LA Phil has credited on a concert programme."""

    name = "laphil"
    base_url = BASE_URL
    cadence = RefreshCadence.MONTHLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument]:
        """Walk the event/person graph, yielding one document per composer.

        ``max_pages`` caps the number of page fetches, for test runs; left
        unset the walk runs to exhaustion, which is the point — the site holds
        more events and people than any index it publishes admits to.
        """
        ingested_at = datetime.now(UTC)
        cache = open_page_cache()
        walk = _Walk(max_pages)
        with make_client() as client:
            walk.seed(seed_urls(fetch_sitemap(client)))
            while (url := walk.next_url()) is not None:
                page = walk.fetch(client, cache, url)
                if page is None:
                    continue
                walk.enqueue(links(page))
                if section(url) == "events":
                    for composer_slug, display in composer_slugs(page).items():
                        # Claimed before the page is read, not after: a composer
                        # is credited on every programme they appear in, and the
                        # same page must not be re-read once per credit.
                        if not walk.claim(composer_slug):
                            continue
                        person = walk.read_person(client, cache, composer_slug)
                        if person is None:
                            continue
                        walk.enqueue(person.event_urls)
                        yield _document(person, display, ingested_at)
                elif section(url) == "people":
                    person = parse_person(slug(url), page)
                    # A page that calls itself a composer is taken at its word;
                    # the walk may not have reached a programme crediting them.
                    if person is not None and person.declares_composer and walk.claim(person.slug):
                        yield _document(person, person.name, ingested_at)
        log.info("laphil: %s", walk.summary(cache))


class _Walk:
    """The frontier, the visited set, and the page budget.

    Event pages are drained ahead of person pages: an event is what names a
    composer, so a run cut short by ``max_pages`` still returns composers.
    """

    def __init__(self, max_pages: int | None) -> None:
        self._events: deque[str] = deque()
        self._people: deque[str] = deque()
        self._queued: set[str] = set()
        self._walked: set[str] = set()
        self._claimed: set[str] = set()
        self._max_pages = max_pages
        self.pages = 0

    def seed(self, urls: Sequence[str]) -> None:
        log.info("laphil: %d seed urls from the sitemap", len(urls))
        self.enqueue(urls)

    def enqueue(self, urls: Iterable[str]) -> None:
        """Queue *urls* in the order given — the sitemap's, then each page's own.

        Not sorted: sorting walks the site alphabetically, which for a run
        stopped early means 60 pages of whatever begins with "a".
        """
        for url in urls:
            if url in self._queued:
                continue
            self._queued.add(url)
            (self._events if section(url) == "events" else self._people).append(url)

    def next_url(self) -> str | None:
        """The next page to fetch, or None when the walk is done or out of budget.

        Skips what has already been read: a composer's page is read the moment a
        programme credits them, which is usually before the queue reaches the
        copy of it that the same programme's links put there.
        """
        while self._events or self._people:
            if self._max_pages is not None and self.pages >= self._max_pages:
                return None
            url = self._events.popleft() if self._events else self._people.popleft()
            if url not in self._walked:
                return url
        return None

    def fetch(self, client: httpx.Client, cache: PageCache | None, url: str) -> str | None:
        self.pages += 1
        self._walked.add(url)
        return fetch_page(client, url, cache)

    def read_person(
        self, client: httpx.Client, cache: PageCache | None, person_slug: str
    ) -> PersonPage | None:
        """A credited composer's own page, mirrored if the walk already passed it.

        Fetched out of turn rather than queued, so the composer is emitted while
        the evidence for it is in hand — a walk that dies mid-sweep still leaves
        a usable snapshot behind, which is how the previous crawl of this site
        ended. The page mirror makes a revisit free; without it this is one
        extra request per composer, once.
        """
        page = self.fetch(client, cache, person_url(person_slug))
        return parse_person(person_slug, page) if page is not None else None

    def claim(self, person_slug: str) -> bool:
        """Whether this walk should read *person_slug* — false if it already has.

        One attempt per composer: a page that could not be fetched is not
        retried from the next programme that credits them, or a site-wide
        outage would be re-tried thousands of times.
        """
        if person_slug in self._claimed:
            return False
        self._claimed.add(person_slug)
        return True

    def summary(self, cache: PageCache | None) -> str:
        mirror = f"; page mirror: {cache.summary()}" if cache is not None else ""
        return f"{self.pages} pages walked, {len(self._claimed)} composers{mirror}"


def _document(person: PersonPage, display_name: str, ingested_at: datetime) -> EntityDocument:
    """One composer, with the claims the page supports and nothing more."""
    claims = [SourceClaim("has_profession", "profession", "composer")]
    if person.born_year:
        claims.append(SourceClaim("born_on", value=person.born_year))
    if person.died_year:
        claims.append(SourceClaim("died_on", value=person.died_year))
    # The programme prints a house-style display name ("BEETHOVEN", "J.S. BACH")
    # that is worth keeping as an alias — but only when it says something the
    # canonical name does not, which capitalisation alone does not.
    if display_name and display_name.casefold() != person.name.casefold():
        claims.append(SourceClaim("also_known_as", value=display_name))
    return EntityDocument(
        id=f"/people/{person.slug}",
        url=person.url,
        source_name=LaPhilAdapter.name,
        ingested_at=ingested_at,
        name=person.name,
        kind=resolve_entity_kind("person", person.name),
        raw={
            "slug": person.slug,
            "given_name": person.given_name,
            "family_name": person.family_name,
            "job_title": person.job_title,
            "programme_name": display_name,
            "image": person.image,
            "artist_id": person.artist_id,
            "born_place": person.born_place,
            "died_place": person.died_place,
            "bio": person.bio,
        },
        claims=tuple(claims),
    )
