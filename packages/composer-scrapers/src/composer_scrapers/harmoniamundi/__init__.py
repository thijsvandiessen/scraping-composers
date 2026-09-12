"""harmonia mundi — the label's catalogue, read from its own pages.

This source used to be crawled. ``harmoniamundi`` was a crawl4ai config feeding
an LLM extractor over ~2100 pages, three extraction kinds deep, and what it
produced was not usable: 985 recording mentions of which **every single one**
had a null release date and a null catalogue number — both printed on every
album page in their own elements — plus roles invented outright (a baritone
credited as ``soprano``, a pianist as ``ensemble``), ``record_key``s pointing at
artist pages so no work ever folded into a release, and album pages read as
*concerts*, one of them dated 1907.

None of it was necessary. The site is WordPress with a hand-filled template,
and every field the model was guessing at is in a named class: the catalogue
number is ``div.feature.ref``, the release month is ``time.release_date``, and
``div.album_humans`` lists each person separately with the role they were
credited under and a link to their page where they have one. So this adapter
reads the catalogue instead of interpreting it — **a person becomes a composer
here only because the label filed them under Composers**, never because a name
looked like a composer's.

One field genuinely is free text: the "Contents" tracklist, a hand-edited box
whose conventions have drifted over thirty years of releases. :mod:`.contents`
reads it from two dependable signals and keeps the whole thing verbatim in the
raw payload besides, so the parser can improve later without re-fetching.

The pieces: :mod:`.urls` turns the sitemaps into the English album and profile
pages, :mod:`.fetch` mirrors them, :mod:`.albums` reads one release,
:mod:`.contents` reads its tracklist, and :mod:`.people` accumulates everyone
credited along the way.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from composer_http import PageCache, open_page_cache

from .. import EntityDocument, RefreshCadence, SourceAdapter, SourceClaim, WorkMentionDocument
from .albums import Album, Credit, parse_album
from .contents import ComposerBlock, WorkEntry, fold
from .fetch import fetch_page, fetch_sitemap_index, fetch_urlset, make_client
from .people import Person, Roster, parse_profile
from .urls import SITE_URL, album_urls, child_sitemaps, page_ref, profile_urls, slug

log = logging.getLogger(__name__)

__all__ = ["SITE_URL", "HarmoniaMundiAdapter"]

#: The label every release on this site carries. The page states no label field
#: — it does not need to — so this is the one value the source asserts by being
#: the source. Lower-cased as the label styles its own name.
LABEL = "harmonia mundi"


@dataclass
class _Sweep:
    """What a run has seen, for the closing summary."""

    albums: int = 0
    unreadable: int = 0
    mentions: int = 0
    contentless: int = 0
    profiles: int = 0
    life_dates: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)


@dataclass
class _Reader:
    """The client, mirror and accumulators one sweep threads through its pages."""

    client: httpx.Client
    cache: PageCache | None
    roster: Roster
    sweep: _Sweep

    def page_lists(self) -> tuple[list[str], list[str]]:
        """The album URLs and profile URLs the sitemaps list, in file order."""
        index = fetch_sitemap_index(self.client)
        album_maps, people_maps = child_sitemaps(index)
        albums = [url for m in album_maps for url in album_urls(fetch_urlset(self.client, m))]
        profiles = [url for m in people_maps for url in profile_urls(fetch_urlset(self.client, m))]
        return albums, profiles

    def album(self, url: str, ingested_at: datetime) -> Iterator[WorkMentionDocument]:
        """One album page, as the works on it."""
        page = fetch_page(self.client, url, self.cache)
        album = parse_album(page, url) if page else None
        if album is None:
            self.sweep.unreadable += 1
            return
        self.sweep.albums += 1
        for credit in album.credits:
            self.roster.add(credit)
        for block in album.contents:
            if block.name and (block.born or block.died):
                self.sweep.life_dates.setdefault(block.name, (block.born, block.died))
        if not any(block.works for block in album.contents):
            self.sweep.contentless += 1
        for document in _mention_documents(album, ingested_at):
            self.sweep.mentions += 1
            yield document

    def profile(self, url: str) -> None:
        """One artist or composer page, folded into the roster."""
        ref = page_ref(url)
        if ref is None:
            return
        page = fetch_page(self.client, url, self.cache)
        profile = parse_profile(page, ref[0], slug(url)) if page else None
        if profile is None:
            return
        self.sweep.profiles += 1
        self.roster.enrich(profile)


class HarmoniaMundiAdapter(SourceAdapter):
    """Every release harmonia mundi lists, and everyone credited on one."""

    name = "harmoniamundi"
    base_url = SITE_URL
    cadence = RefreshCadence.MONTHLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        """The catalogue, as work mentions first and the people on them after.

        ``max_pages`` caps the number of *album* pages read, for smoke runs. The
        profile pages are few and always read in full, so a capped run still
        ends with every artist and composer the label publishes.

        Mentions are yielded as each album lands rather than collected, so a
        sweep that dies partway still leaves a usable snapshot. The people can
        only come last: someone's profession is settled by every credit they
        appear under, which is not known until the albums have been read.
        """
        ingested_at = datetime.now(UTC)
        cache = open_page_cache()
        sweep = _Sweep()
        roster = Roster()
        with make_client() as client:
            reader = _Reader(client, cache, roster, sweep)
            albums, profiles = reader.page_lists()
            if max_pages is not None:
                albums = albums[:max_pages]
            log.info("harmoniamundi: %d album pages, %d profile pages", len(albums), len(profiles))
            for url in albums:
                yield from reader.album(url, ingested_at)
            for url in profiles:
                reader.profile(url)
        roster.life_dates(sweep.life_dates)
        folded = roster.reconcile()
        for person in roster:
            yield _person_document(person, ingested_at)
        log.info(
            "harmoniamundi: %d albums (%d unreadable), %d work mentions (%d albums with no tracklist), "
            "%d profiles, %d people (%d bare names folded in); page mirror: %s",
            sweep.albums,
            sweep.unreadable,
            sweep.mentions,
            sweep.contentless,
            sweep.profiles,
            len(roster),
            folded,
            cache.summary() if cache is not None else "off",
        )


def _work_key(title: str, seen: dict[str, int]) -> str:
    """A stable, album-local key for one work.

    The folded title rather than its position in the listing: this key becomes
    the document's ``external_id``, which silver treats as the identity of the
    mention, and an ordinal would shift every work below the one the label
    edited. Repeats within one album — the same title twice on a two-disc set —
    are numbered.
    """
    base = fold(title) or "untitled"
    seen[base] = seen.get(base, 0) + 1
    return base if seen[base] == 1 else f"{base}-{seen[base]}"


def _role(credit: Credit) -> str:
    """A credit's role in the vocabulary ``recordings/derive`` understands.

    Ensemble-ness comes from :attr:`~.albums.Credit.is_ensemble`, the same test
    the roster kinds the entity with, so a participant's role on a recording and
    the kind of the entity behind it can never disagree.
    """
    if credit.is_ensemble:
        return "ensemble"
    return "conductor" if credit.role.casefold() == "conductor" else "soloist"


def _mention_documents(album: Album, ingested_at: datetime) -> Iterator[WorkMentionDocument]:
    """Every work on a release, or the release itself when it lists none.

    An album whose tracklist could not be read still yields one mention, titled
    after the album. Without it the release produces no mentions, and
    ``derive_recordings`` — which groups mentions — would produce no recording
    row for it at all, losing the catalogue number and credits along with the
    works.
    """
    seen: dict[str, int] = {}
    entries = [(block, work) for block in album.contents for work in block.works]
    if not entries:
        yield _mention_document(album, None, WorkEntry(album.title), ingested_at, seen)
        return
    for block, work in entries:
        yield _mention_document(album, block, work, ingested_at, seen)


def _mention_document(
    album: Album,
    block: ComposerBlock | None,
    work: WorkEntry,
    ingested_at: datetime,
    seen: dict[str, int],
) -> WorkMentionDocument:
    """One work on one release.

    The ``raw`` payload is what ``recordings/derive`` reads, and it is shaped for
    it: ``record_key`` is the album, so every work on a release folds into one
    recording rather than one per work, and ``artists`` carries the roles that
    pass already understands.

    ``tracks`` holds this work's own movements, as decca's payload does — not
    the whole album's, which would repeat the same list on every work of a
    30-work recital. The verbatim ``contents`` covers that case instead, and is
    the input to any later, better parse of the tracklist.
    """
    return WorkMentionDocument(
        id=f"/albums/{album.slug}/works/{quote(_work_key(work.title, seen), safe='')}",
        url=album.url,
        source_name=HarmoniaMundiAdapter.name,
        ingested_at=ingested_at,
        title=work.title,
        composer=block.composer if block else None,
        raw={
            "_source": "scraper",
            "_kind": "recording",
            "record_key": f"album:{album.slug}",
            "title": album.title,
            "release_date": album.release_date,
            "label": LABEL,
            "catalogue_number": album.catalogue_number,
            "format": album.format,
            "url": album.url,
            "artists": [
                {"name": credit.name, "role": _role(credit), "discipline": credit.discipline}
                for credit in album.performers
            ],
            "album_slug": album.slug,
            "work_subtitle": work.subtitle,
            "work_source": "contents" if block is not None else "album",
            "composer_heading": (
                {"name": block.name, "born": block.born, "died": block.died} if block else None
            ),
            "tracks": [track.as_raw() for track in work.tracks],
            "contents": album.contents_text,
            "composer_credits": [
                {"name": credit.name, "slug": credit.ref[1] if credit.ref else None}
                for credit in album.composer_credits
            ],
            "composers_display": album.composers_display,
            "artists_display": album.artists_display,
            "duration": album.duration,
            "image": album.image,
        },
    )


def _person_document(person: Person, ingested_at: datetime) -> EntityDocument:
    """One person or ensemble, with the claims their credits support and no more."""
    claims: list[SourceClaim] = []
    profession = person.profession
    if profession is not None:
        claims.append(SourceClaim("has_profession", "profession", profession))
    claims += [SourceClaim("performs_as", value=discipline) for discipline in person.disciplines]
    if person.born:
        claims.append(SourceClaim("born_on", value=person.born))
    if person.died:
        claims.append(SourceClaim("died_on", value=person.died))
    return EntityDocument(
        id=person.external_id,
        url=person.url,
        source_name=HarmoniaMundiAdapter.name,
        ingested_at=ingested_at,
        name=person.name,
        kind=person.kind,
        raw={
            "slug": person.ref[1] if person.ref else None,
            "page_kind": person.ref[0] if person.ref else None,
            "roles": sorted(person.roles),
            "columns": sorted(person.columns),
            "hero": person.hero,
            "bio": person.bio,
            "albums": person.albums,
        },
        claims=tuple(claims),
    )
