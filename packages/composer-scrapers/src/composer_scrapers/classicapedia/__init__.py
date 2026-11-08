"""Classicapedia: biographies, teacher-pupil links, works, venues and orchestras.

A lower-trust source: one author's personal encyclopedia, with no citation on any
link and no identifier on any person. What it adds is reach (22k people, many
teacher links Wikidata lacks). Provenance is per claim, so anything only this
source says stays identifiable. See ``docs/classicapedia.md``.

The crawl comes first and the documents after, because a teacher's identity is
only known once their own page has been read:

1. the sitemap and the search index, live;
2. every profile page, through the mirror;
3. the Wikipedia articles those pages link, resolved to Wikidata items;
4. a Wikidata search for the people who share a name, who have no other identity;
5. the documents, naming each person the way their own record does.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from composer_http import SourceSession

from .. import EntityDocument, HttpSourceAdapter, RefreshCadence, SourceClaim, WorkMentionDocument
from ..wikidata.ensembles import ENSEMBLE_CLASSES, MUSICAL_GROUPS
from .fetch import (
    BASE_URL,
    BIOGRAPHY,
    ORCHESTRA,
    REQUEST_DELAY_S,
    SECTIONS,
    IndexEntry,
    PageRef,
    fetch_index,
    fetch_sitemap,
    iter_pages,
    page_refs,
    parse_index,
)
from .people import Biography, Relation, parse_biography
from .resolve import (
    WikidataItem,
    confirms_namesake,
    confirms_person,
    search_people,
    wikidata_items,
    wikipedia_qids,
)
from .venues import Profile, parse_profile
from .works import ListedWork, parse_works

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "ClassicapediaAdapter"]

Doc = EntityDocument | WorkMentionDocument

SOURCE_NAME = "classicapedia"
PERSON = "person"
VENUE = "venue"
ENSEMBLE = "ensemble"
_WIKIDATA_PAGE = "https://www.wikidata.org/wiki/"

#: The index's activities that name a profession outright. The rest are either
#: instrument families (``teclado``, ``cuerda``) or mixed bags (``libretista`` holds
#: playwrights, ``teorico`` critics), and stay in ``raw``.
_PROFESSIONS = {
    "compositor": "composer",
    "batuta": "conductor",
    "director": "conductor",
    "cantante": "singer",
    "pedagogo": "teacher",
    "constructor": "instrument maker",
}
_COMPOSER_ACTIVITY = "compositor"

#: Opera house, concert hall and theatre building. An item that is one of these
#: stays a venue even if it is also typed as a company.
_BUILDING_CLASSES = frozenset({"Q153562", "Q1060829", "Q24354"})
_GROUP_CLASSES = frozenset({*ENSEMBLE_CLASSES, *MUSICAL_GROUPS})

_NON_WORD = re.compile(r"[^\w\s]")


def name_key(name: str) -> str:
    """What the warehouse's label key sees of *name*: two names with one key land
    on one entity. A copy of the idea rather than an import, since this tier does
    not depend on ``composer_models``."""
    decomposed = unicodedata.normalize("NFKD", name)
    folded = "".join(char for char in decomposed if not unicodedata.combining(char)).lower()
    return " ".join(_NON_WORD.sub("", folded).split())


def shared_names(entries: Iterable[IndexEntry]) -> frozenset[str]:
    """The name keys more than one person on the site answers to."""
    counts = Counter(name_key(entry.name) for entry in entries)
    return frozenset(key for key, count in counts.items() if count > 1)


def classify_kind(section: str, item: WikidataItem | None) -> str:
    """The kind a venue or orchestra page describes.

    The site files opera companies under opera houses (Welsh National Opera), so
    for those two sections Wikidata decides. Direct classes only, with no subclass
    walk, which suits a one-in-a-hundred correction.
    """
    if section == ORCHESTRA:
        return ENSEMBLE
    if item is None or item.instance_of & _BUILDING_CLASSES:
        return VENUE
    return ENSEMBLE if item.instance_of & _GROUP_CLASSES else VENUE


def page_url(section: str, slug: str) -> str:
    return f"{BASE_URL}/en/{section}/{slug}.html"


@dataclass(frozen=True)
class _Identity:
    """How a person is named everywhere this source mentions them."""

    label: str
    qid: str | None


class _People:
    """Who each slug is, and which people cannot safely be named at all."""

    def __init__(
        self,
        entries: list[IndexEntry],
        pinned: dict[str, WikidataItem],
    ) -> None:
        self.entries = {entry.slug: entry for entry in entries}
        self.pinned = pinned
        self.shared = shared_names(entries)

    def is_shared(self, name: str) -> bool:
        return name_key(name) in self.shared

    def _pinned_identity(self, slug: str, fallback: str) -> _Identity:
        item = self.pinned[slug]
        return _Identity(item.label or fallback, item.qid)

    def of_slug(self, slug: str, fallback: str) -> _Identity | None:
        """The identity of the biography at *slug*; ``None`` when the name alone
        would merge two people and no verified QID tells them apart."""
        entry = self.entries.get(slug)
        name = entry.name if entry is not None else fallback
        if slug in self.pinned:
            return self._pinned_identity(slug, name)
        return None if self.is_shared(name) else _Identity(name, None)


def _relation_claims(
    predicate: str,
    subject_slug: str,
    relations: Iterable[Relation],
    people: _People,
    tally: Counter[str],
) -> Iterator[SourceClaim]:
    seen: set[tuple[str, str | None]] = set()
    for relation in relations:
        if relation.slug is None:
            # The site could not link it, and a name is all there is. The full crawl
            # showed what matching those to pages gives: of 388, 191 were the page's
            # own subject and 47 a teacher dead before the pupil's birth (Luise Walker's
            # guitar teacher Heinrich Albert became the baroque composer). Linked: none
            # of 11,301. So they stay in ``raw``.
            tally["unlinked names not claimed"] += 1
            continue
        if relation.slug == subject_slug:
            continue
        identity = people.of_slug(relation.slug, relation.name)
        if identity is None:
            tally["links dropped"] += 1
            continue
        if (identity.label, identity.qid) in seen:
            continue
        seen.add((identity.label, identity.qid))
        tally["teacher links"] += 1
        yield SourceClaim(predicate, PERSON, identity.label, object_wikidata_id=identity.qid)


def _person_claims(
    bio: Biography, entry: IndexEntry | None, people: _People, tally: Counter[str]
) -> list[SourceClaim]:
    claims: list[SourceClaim] = []
    if bio.birth_year:
        claims.append(SourceClaim("born_on", value=bio.birth_year))
    if bio.death_year:
        claims.append(SourceClaim("died_on", value=bio.death_year))
    if bio.birth_place:
        claims.append(SourceClaim("born_in", "place", bio.birth_place))
    activities = entry.activities if entry is not None else ()
    professions = dict.fromkeys(_PROFESSIONS[a] for a in activities if a in _PROFESSIONS)
    claims += [SourceClaim("has_profession", "profession", profession) for profession in professions]
    claims += _relation_claims("student_of", bio.slug, bio.teachers, people, tally)
    claims += _relation_claims("teacher_of", bio.slug, bio.pupils, people, tally)
    return claims


def _person_raw(bio: Biography, entry: IndexEntry | None, url: str) -> dict[str, Any]:
    """Every fact the page states; never its prose."""
    return {
        "page_url": url,
        "site_name": bio.name,
        "dates": bio.dates,
        "occupation": bio.occupation,
        "activities": list(entry.activities) if entry is not None else [],
        "nationality": entry.nationality if entry is not None else "",
        "teachers": [relation.as_dict() for relation in bio.teachers],
        "pupils": [relation.as_dict() for relation in bio.pupils],
        "wikipedia": list(bio.wikipedia) if bio.wikipedia else None,
    }


def _work_id(slug: str, work: ListedWork) -> str:
    """Stable across runs, and distinct for the same title listed under two
    genres or with two dates."""
    digest = hashlib.sha256(f"{work.genre}|{work.verbatim}|{work.years}".encode()).hexdigest()
    return f"{BIOGRAPHY}/{slug}#{digest[:16]}"


class ClassicapediaAdapter(HttpSourceAdapter[Doc]):
    name = SOURCE_NAME
    base_url = BASE_URL
    # Regenerated in batches, and a re-crawl costs hours: once a year is plenty.
    cadence = RefreshCadence.YEARLY
    request_delay_s = REQUEST_DELAY_S

    def scrape(self, session: SourceSession, max_pages: int | None = None) -> Iterator[Doc]:
        """Crawl, resolve, then yield people, their works, venues and orchestras.

        ``max_pages`` caps the pages requested per sitemap section, for test runs,
        so that a small run still reaches every kind of page.
        """
        ingested_at = datetime.now(UTC)
        tally: Counter[str] = Counter()
        refs = page_refs(fetch_sitemap(session))
        entries = parse_index(fetch_index(session))
        if not entries or not any(ref.section == BIOGRAPHY for ref in refs):
            # Finishing "successfully" with no records would hide a site change.
            raise RuntimeError("classicapedia: the sitemap or search index lists no biographies")

        pages, works, profiles = self._crawl(session, refs, max_pages, tally)
        items_by_article = self._resolve(session, pages, profiles)
        pinned = self._pin_people(pages, items_by_article, tally)
        pinned |= self._pin_namesakes(session, pages, entries, pinned, tally)
        people = _People(entries, pinned)

        for bio in pages:
            yield from self._person_documents(bio, works[bio.slug], people, ingested_at, tally)
        for profile in profiles:
            item = items_by_article.get(profile.wikipedia) if profile.wikipedia else None
            yield self._profile_document(profile, item, ingested_at, tally)
        log.info("classicapedia: %s", ", ".join(f"{count} {what}" for what, count in sorted(tally.items())))

    def _crawl(
        self,
        session: SourceSession,
        refs: list[PageRef],
        max_pages: int | None,
        tally: Counter[str],
    ) -> tuple[list[Biography], dict[str, tuple[ListedWork, ...]], list[Profile]]:
        biographies = [ref for ref in refs if ref.section == BIOGRAPHY]
        pages: list[Biography] = []
        works: dict[str, tuple[ListedWork, ...]] = {}
        for ref, page in iter_pages(session, biographies, max_pages):
            bio = parse_biography(ref.slug, page)
            if bio is None:
                tally["unreadable pages"] += 1
            elif bio.is_family:
                tally["families skipped"] += 1
            else:
                pages.append(bio)
                works[bio.slug] = parse_works(page)
        profiles: list[Profile] = []
        for section in SECTIONS:
            if section == BIOGRAPHY:
                continue
            listed = [ref for ref in refs if ref.section == section]
            for ref, page in iter_pages(session, listed, max_pages):
                profile = parse_profile(ref.section, ref.slug, page)
                if profile is None:
                    tally["unreadable pages"] += 1
                else:
                    profiles.append(profile)
        return pages, works, profiles

    def _resolve(
        self, session: SourceSession, pages: list[Biography], profiles: list[Profile]
    ) -> dict[tuple[str, str], WikidataItem]:
        """The Wikidata item behind every article the crawl met."""
        articles = {bio.wikipedia for bio in pages if bio.wikipedia}
        articles |= {profile.wikipedia for profile in profiles if profile.wikipedia}
        qids = wikipedia_qids(session, articles)
        items = wikidata_items(session, qids.values())
        return {article: items[qid] for article, qid in qids.items() if qid in items}

    def _pin_people(
        self,
        pages: list[Biography],
        items: dict[tuple[str, str], WikidataItem],
        tally: Counter[str],
    ) -> dict[str, WikidataItem]:
        pinned: dict[str, WikidataItem] = {}
        for bio in pages:
            item = items.get(bio.wikipedia) if bio.wikipedia else None
            if item is None:
                continue
            if confirms_person(item, bio.birth_year, bio.death_year):
                pinned[bio.slug] = item
            else:
                tally["QIDs rejected (not this person)"] += 1
        tally["people pinned to a QID"] = len(pinned)
        return pinned

    def _pin_namesakes(
        self,
        session: SourceSession,
        pages: list[Biography],
        entries: list[IndexEntry],
        pinned: dict[str, WikidataItem],
        tally: Counter[str],
    ) -> dict[str, WikidataItem]:
        """QIDs for people dropped for sharing a name, found by name and both years.

        Exactly one candidate must fit, and a QID two pages arrive at, or one a page
        already cites, pins nobody: a wrong QID is a hard cannot-link in person dedupe.
        """
        shared = shared_names(entries)
        names = {entry.slug: entry.name for entry in entries}
        wanted = [
            bio
            for bio in pages
            if bio.slug not in pinned
            and bio.birth_year
            and bio.death_year
            and name_key(names.get(bio.slug, bio.name)) in shared
        ]
        candidates = {bio.slug: search_people(session, names.get(bio.slug, bio.name)) for bio in wanted}
        items = wikidata_items(session, {qid for qids in candidates.values() for qid in qids})
        found: dict[str, WikidataItem] = {}
        for bio in wanted:
            fits = [
                items[qid]
                for qid in candidates[bio.slug]
                if qid in items and confirms_namesake(items[qid], bio.birth_year, bio.death_year)
            ]
            if len(fits) == 1:
                found[bio.slug] = fits[0]
            elif fits:
                tally["namesakes with several fitting items"] += 1
        taken = Counter(item.qid for item in (*pinned.values(), *found.values()))
        kept = {slug: item for slug, item in found.items() if taken[item.qid] == 1}
        tally["namesake QIDs refused (already taken)"] += len(found) - len(kept)
        tally["people pinned by name and years"] = len(kept)
        return kept

    def _person_documents(
        self,
        bio: Biography,
        works: tuple[ListedWork, ...],
        people: _People,
        ingested_at: datetime,
        tally: Counter[str],
    ) -> Iterator[Doc]:
        identity = people.of_slug(bio.slug, bio.name)
        if identity is None:
            tally["people dropped (shared name)"] += 1
            return
        url = page_url(BIOGRAPHY, bio.slug)
        entry = people.entries.get(bio.slug)
        tally["people"] += 1
        yield EntityDocument(
            id=f"{BIOGRAPHY}/{bio.slug}",
            # Ingest pins a record to a QID through its URL alone.
            url=f"{_WIKIDATA_PAGE}{identity.qid}" if identity.qid else url,
            source_name=self.name,
            ingested_at=ingested_at,
            name=identity.label,
            kind=PERSON,
            raw=_person_raw(bio, entry, url),
            claims=tuple(_person_claims(bio, entry, people, tally)),
        )
        if entry is None or _COMPOSER_ACTIVITY not in entry.activities:
            return
        if people.is_shared(entry.name):
            # A mention finds its composer by label alone, so even a pinned person
            # cannot take works when the label also names somebody else.
            tally["composers without works (shared name)"] += 1
            return
        seen: set[str] = set()
        for work in works:
            work_id = _work_id(bio.slug, work)
            if work_id in seen:
                continue
            seen.add(work_id)
            tally["work mentions"] += 1
            yield WorkMentionDocument(
                id=work_id,
                url=url,
                source_name=self.name,
                ingested_at=ingested_at,
                title=work.title,
                composer=entry.name,
                raw={"page_url": url, **work.as_raw()},
            )

    def _profile_document(
        self,
        profile: Profile,
        item: WikidataItem | None,
        ingested_at: datetime,
        tally: Counter[str],
    ) -> EntityDocument:
        kind = classify_kind(profile.section, item)
        tally[kind + "s"] += 1
        if item is None:
            tally["profiles without a QID"] += 1
        elif kind == VENUE and not item.instance_of & _BUILDING_CLASSES:
            tally["venues of an unrecognised class"] += 1
        url = page_url(profile.section, profile.slug)
        claims: list[SourceClaim] = []
        if profile.city:
            claims.append(SourceClaim("based_in", "place", profile.city))
        if profile.opened:
            claims.append(SourceClaim("opened_on", value=profile.opened))
        if profile.founded:
            claims.append(SourceClaim("founded_on", value=profile.founded))
        if profile.wikipedia:
            language, title = profile.wikipedia
            article = f"https://{language}.wikipedia.org/wiki/{title.replace(' ', '_')}"
            claims.append(SourceClaim("wikipedia_article", value=article))
        return EntityDocument(
            id=f"{profile.section}/{profile.slug}",
            url=f"{_WIKIDATA_PAGE}{item.qid}" if item is not None else url,
            source_name=self.name,
            ingested_at=ingested_at,
            name=profile.name,
            kind=kind,
            raw={
                "page_url": url,
                "section": profile.section,
                "city": profile.city,
                "country": profile.country,
                "facts": dict(profile.facts),
                "instance_of": sorted(item.instance_of) if item is not None else [],
            },
            claims=tuple(claims),
        )
