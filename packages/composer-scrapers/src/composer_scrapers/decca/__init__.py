"""Decca Classics — the recorded catalogue, read from the label's own API.

This source used to be crawled twice over. ``decca`` and ``deccaclassics`` were
two crawl configs pointed at the same seed with the same allow-pattern, running
crawl4ai into an LLM extractor — three passes per page for one of them, one for
the other — and between them they produced 224 and 318 partly-overlapping
recordings, a handful of "concerts" scraped off news pages, and dates like
``2013-08-XX`` that no downstream consumer could read. The composer of a work
was whatever the model made of a marketing blurb.

None of it was necessary. deccaclassics.com is a Next.js front end over a public
GraphQL API that states, per track, the works performed and the function every
contributor was credited under. So this adapter reads the catalogue instead of
interpreting it: **a person becomes a composer here only because the label filed
a track under them as one** — never because a name looked like a composer's.

What the swap buys, measured over a 60-family sample: a level-1 work on 100% of
tracks, an ISRC on 96%, a composer on 87%, a recording location on 82%, and
complete box sets — the rendered page only ever shows disc 1 (see :mod:`.fetch`).

The pieces: :mod:`.urls` turns the sitemap into the 3575 product families that
*are* the catalogue, :mod:`.fetch` batches them out of the API, :mod:`.products`
reads one family as recordings and works, and :mod:`.artists` accumulates the
people credited along the way.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from composer_http import open_page_cache

from .. import EntityDocument, RefreshCadence, SourceAdapter, SourceClaim, WorkMentionDocument
from .artists import Person, Roster
from .fetch import fetch_artists, fetch_families, fetch_sitemap, make_client
from .products import Recording, WorkMention, recordings
from .urls import SITE_URL, artist_slugs, families

log = logging.getLogger(__name__)

__all__ = ["SITE_URL", "DeccaAdapter"]


class DeccaAdapter(SourceAdapter):
    """Every recording Decca Classics lists, and everyone credited on one."""

    name = "decca"
    base_url = SITE_URL
    cadence = RefreshCadence.MONTHLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        """The catalogue, as work mentions first and the people on them after.

        ``max_pages`` caps the number of *product families* read, for smoke
        runs. The roster is small and fixed, so it is always read in full — a
        capped run still ends with a complete set of artist pages.

        Mentions are yielded as each family lands rather than collected, so a
        sweep that dies partway still leaves a usable snapshot. The people can
        only come last: an artist's profession is settled by every credit they
        appear under, which is not known until the recordings have been read.
        """
        ingested_at = datetime.now(UTC)
        cache = open_page_cache()
        roster = Roster()
        with make_client() as client:
            sitemap = fetch_sitemap(client)
            catalogue = families(sitemap)
            slugs = artist_slugs(sitemap)
            ids = list(catalogue)[:max_pages] if max_pages is not None else list(catalogue)
            log.info("decca: %d product families, %d roster pages", len(ids), len(slugs))
            mentions = 0
            for family in fetch_families(client, ids, cache):
                family_id = family.get("idRaw")
                if not isinstance(family_id, int):
                    continue
                for recording in recordings(family, catalogue.get(family_id, str(family_id))):
                    _collect(roster, recording)
                    for mention in recording.mentions:
                        mentions += 1
                        yield _mention_document(recording, mention, ingested_at)
            for node in fetch_artists(client, slugs, cache):
                roster.enrich(node)
        folded = roster.reconcile()
        for person in roster:
            yield _person_document(person, ingested_at)
        log.info(
            "decca: %d work mentions, %d artists (%d heading-only names folded in); page mirror: %s",
            mentions,
            len(roster),
            folded,
            cache.summary() if cache is not None else "off",
        )


def _collect(roster: Roster, recording: Recording) -> None:
    """Fold one recording's credits into the roster.

    Composers are added from the recording's composer credits rather than from
    the participant list, which excludes them on purpose — a composer is not a
    performer on their own recording.
    """
    for credit in recording.composers:
        roster.add(credit, composer=True)
    for credit in recording.participants:
        roster.add(credit)
    for credit in recording.production:
        roster.add(credit)
    roster.life_dates(recording.life_dates)


def _mention_document(
    recording: Recording, mention: WorkMention, ingested_at: datetime
) -> WorkMentionDocument:
    """One work on one release.

    The ``raw`` payload is what ``recordings/derive`` reads, and it is shaped
    for it: ``record_key`` is the product, so every work on a release folds into
    one recording rather than one per work, and ``artists`` carries the roles
    that pass already understands. The track detail rides along beside it —
    ISRCs, durations, recording dates and locations that nothing consumes yet,
    but which are the reason to read this API rather than the page.
    """
    return WorkMentionDocument(
        id=f"/products/{recording.product_id}/works/{mention.work_id}",
        url=recording.url,
        source_name=DeccaAdapter.name,
        ingested_at=ingested_at,
        title=mention.title,
        composer=mention.composer,
        raw={
            "_source": "scraper",
            "_kind": "recording",
            "record_key": recording.record_key,
            "title": recording.title,
            "release_date": recording.release_date,
            "label": recording.label,
            "catalogue_number": recording.catalogue_number,
            "format": recording.format,
            "url": recording.url,
            "artists": [
                {"name": credit.name, "role": credit.role, "discipline": credit.function or None}
                for credit in recording.participants
            ],
            "work_id": mention.work_id,
            "composers": list(mention.composers),
            "product_id": recording.product_id,
            "family_id": recording.family_id,
            "category": recording.category,
            "tracks": [track.as_raw() for track in mention.tracks],
            "production": [
                {"name": credit.name, "function": credit.function} for credit in recording.production
            ],
        },
    )


def _person_document(person: Person, ingested_at: datetime) -> EntityDocument:
    """One artist, with the claims their credits support and nothing more."""
    claims: list[SourceClaim] = []
    profession = person.profession
    if profession is not None:
        claims.append(SourceClaim("has_profession", "profession", profession))
    claims += [SourceClaim("performs_as", value=discipline) for discipline in person.disciplines]
    if person.born:
        claims.append(SourceClaim("born_on", value=person.born))
    if person.died:
        claims.append(SourceClaim("died_on", value=person.died))
    # The catalogue's sort form ("Larrocha, Alicia de") says something the
    # display name does not — where the family name starts.
    if person.sort_name and person.sort_name.casefold() != person.name.casefold():
        claims.append(SourceClaim("also_known_as", value=person.sort_name))
    return EntityDocument(
        id=person.external_id,
        url=person.url,
        source_name=DeccaAdapter.name,
        ingested_at=ingested_at,
        name=person.name,
        kind=person.kind,
        raw={
            "artist_id": person.artist_id,
            "slug": person.url_alias,
            "sort_name": person.sort_name,
            "functions": sorted(person.functions),
            "theme_type": person.theme_type,
            "promoted_artist": person.flagged_composer,
            "bio": person.bio,
        },
        claims=tuple(claims),
    )
