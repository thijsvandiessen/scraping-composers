"""Royal Opera House Collections — the Covent Garden performance database.

Every opera, ballet and concert given at the Royal Opera House since the 1730s:
948 works, their ~1,140 stagings, and the ~15,700 individual nights, with cast,
conductor and the archival document each record was catalogued from. 926 of the
works name a composer, 340 distinct. It is the deepest performance archive of
the sources here, and the only one reaching back to the eighteenth century.

**The obvious way in is the wrong one.** The site's own performance search —
``SearchResults.aspx?searchtype=performance&genre=Opera`` — paginates 5,690
opera performances twenty to a page, and neither the listing nor the performance
page behind it names a composer. Carmen, 14 January 1947, records the company,
the venue, the conductor, thirty-three cast members, the translator and the
programme it was catalogued from; it does not record Bizet. Six thousand
requests would yield no composers at all.

The composer is one level up, on the work page, in a labelled row. So this
adapter enumerates through ``performanceindex.aspx`` instead — 27 letter pages,
no pagination, all 995 rows with their genre — and descends
work → production → performance from there. See :mod:`.index` for why 27 pages
replace 773, and :mod:`.listings` for the nights that hang off a work with
no production between them and are lost by a walk that assumes the hierarchy is
strict.

**What a work's title means depends on its genre**, and getting it wrong
manufactures works nobody wrote. A ballet page names both the choreographer and
the composer, and its title is the choreographer's: "Adagio Hammerklavier" is
Hans van Manen's, over a Beethoven sonata the page names on the next row. The
index compounds this by printing one bracketed name after every title — the
composer for an opera, the choreographer for a ballet, with nothing to say
which. So the bracketed name is never read as a composer, and the pairing is
always ``Composer:`` with ``Music title:`` where the page distinguishes one
(:mod:`.works`).

**One document per work, and one per night.** The work yields a mention carrying
the composer, librettist, premieres and production list; each of its
performances yields another for the same work, carrying that night's date,
venue, company, conductor and cast so the warehouse can rebuild the evening —
the same shape the wienerphil adapter emits per programme item. The
composer on a performance mention comes from the work above it — there is
nowhere else it could come from. People are emitted last, once, from the
registry in :mod:`.people`.

**The sweep is long and the mirror is what makes it survivable.** robots.txt asks
for a 180-second crawl delay; this adapter uses five, a departure agreed with
the repository owner and documented in :mod:`.fetch`. Even so a full run is
~18,000 pages and more than a day. Every page goes through
:class:`~composer_http.PageCache`, so a run that dies at hour twenty resumes
where it stopped, and re-parsing the cast tables — the least regular markup on
the site — costs nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from composer_http import PageCache, open_page_cache

from .. import EntityDocument, RefreshCadence, SourceAdapter, WorkMentionDocument
from .credits import Credit
from .dates import iso_date, place
from .fetch import fetch_index, fetch_page, make_client
from .index import IndexEntry, entries
from .listings import PerformanceRef
from .people import Person, Registry
from .performances import PerformancePage
from .performances import parse as parse_performance
from .productions import ProductionPage
from .productions import parse as parse_production
from .urls import BASE_URL, LETTERS, external_id, performance_url, production_url, work_url
from .works import WorkPage
from .works import parse as parse_work

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "RohAdapter"]

#: How often a sweep of eighteen thousand pages says where it is. At the polite
#: delay this is roughly one line every eight minutes; hours of silence is not a
#: progress report.
_PROGRESS_EVERY = 100


class RohAdapter(SourceAdapter):
    """Every work the Royal Opera House has staged, and every night it ran."""

    name = "roh"
    base_url = BASE_URL
    cadence = RefreshCadence.MONTHLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        """Yield a mention per work, then one per performance, then the people.

        ``max_pages`` caps the number of *record* pages read — work, production
        and performance — as in the wienerphil and laphil adapters. The 27 index
        pages are never capped: they are the only enumerator this source has, so
        a short run should still know the full shape of what it did not read.

        The tiers are drained in order, and that order is the point: every
        composer in the database is known once the work tier finishes, at ~1,000
        pages of the ~18,000. A run cut short after it still returns a complete
        composer set, and loses only the performance detail.
        """
        ingested_at = datetime.now(UTC)
        cache = open_page_cache()
        registry = Registry()

        with make_client() as client:
            sweep = _Sweep(client, cache, max_pages)
            listing = _enumerate(client)
            yield from _read_works(sweep, listing, registry, ingested_at)
            _read_productions(sweep, registry)
            yield from _read_performances(sweep, registry, ingested_at)

        log.info("roh: %s", sweep.summary(registry))
        if sweep.unknown:
            # Not an error: an unrecognised label costs one credit, not a run.
            # Reported so the vocabularies in .credits can be completed.
            log.warning("roh: labels in no vocabulary: %s", ", ".join(sorted(sweep.unknown)))
        for record in registry.records():
            yield EntityDocument(
                id=record.external_id,
                url=None,
                source_name=RohAdapter.name,
                ingested_at=ingested_at,
                name=record.name,
                kind=record.kind,
                raw=record.raw,
                claims=record.claims,
            )


# ---------------------------------------------------------------------------
# The sweep: budget, queues, and what the run learned.
# ---------------------------------------------------------------------------


@dataclass
class _Sweep:
    """The page budget, the work of each tier still to do, and the tallies.

    The queues are plain lists rather than a frontier: this walk is a tree with
    a known shape — every production and every performance is reached from
    exactly one parent — so there is nothing to deduplicate and nothing to
    revisit. That is the difference between this source and the laphil graph
    walk next door.
    """

    client: httpx.Client
    cache: PageCache | None
    max_pages: int | None
    pages: int = 0
    works: dict[int, WorkPage] = field(default_factory=dict)
    #: ``(production_id, work_id)`` — a production page never says which work it
    #: stages, so the parent is carried down with it. See :mod:`.productions`.
    productions: list[tuple[int, int]] = field(default_factory=list)
    #: ``(work_id, production_id | None, ref)`` for each night still to read.
    performances: list[tuple[int, int | None, PerformanceRef]] = field(default_factory=list)
    stubs: int = 0
    unreadable: int = 0
    mentions: int = 0
    unknown: set[str] = field(default_factory=set)

    @property
    def exhausted(self) -> bool:
        return self.max_pages is not None and self.pages >= self.max_pages

    def fetch(self, url: str) -> str | None:
        """One record page, against the budget. None if it could not be read."""
        self.pages += 1
        page = fetch_page(self.client, url, self.cache)
        if page is None:
            self.unreadable += 1
        if self.pages % _PROGRESS_EVERY == 0:
            log.info(
                "roh: %d pages read (%d works, %d productions and %d performances queued)",
                self.pages,
                len(self.works),
                len(self.productions),
                len(self.performances),
            )
        return page

    def summary(self, registry: Registry) -> str:
        mirror = f"; page mirror: {self.cache.summary()}" if self.cache is not None else ""
        return (
            f"{self.pages} pages, {len(self.works)} works ({self.stubs} cross-reference stubs), "
            f"{self.mentions} work mentions, {len(registry)} people, "
            f"{self.unreadable} unreadable{mirror}"
        )


def _enumerate(client: httpx.Client) -> list[IndexEntry]:
    """Every work in the database, from the 27 letter pages of the index.

    Not budgeted and not mirrored — see :meth:`RohAdapter.fetch` and
    :mod:`.fetch`. A letter that cannot be read raises rather than shortening
    the sweep silently.
    """
    listing = [entry for letter in LETTERS for entry in entries(fetch_index(client, letter))]
    log.info("roh: %d works in the index", len(listing))
    return listing


# ---------------------------------------------------------------------------
# Tier 1: works. Every composer in the database is known when this finishes.
# ---------------------------------------------------------------------------


def _read_works(
    sweep: _Sweep, listing: list[IndexEntry], registry: Registry, ingested_at: datetime
) -> Iterator[WorkMentionDocument]:
    for entry in listing:
        if sweep.exhausted:
            break
        if entry.work_id in sweep.works:
            # Each work is filed under one initial today, so this never fires;
            # it costs one lookup and stops a work listed twice from being
            # emitted as two documents under one id.
            continue
        page = sweep.fetch(work_url(entry.work_id))
        if page is None:
            continue
        work = parse_work(entry.work_id, page)
        sweep.unknown |= work.unknown
        if work.is_stub:
            # A pointer at another entry — "The Barber of Seville: see 'Il
            # barbiere di Siviglia'". It names no one and stages nothing.
            sweep.stubs += 1
            continue
        sweep.works[work.work_id] = work
        _record_credits(registry, work.credits, genre=work.genre, work_id=work.work_id)
        sweep.productions.extend((ref.production_id, work.work_id) for ref in work.productions)
        sweep.performances.extend((work.work_id, None, ref) for ref in work.unlinked)
        sweep.mentions += 1
        yield _work_document(work, entry, ingested_at)
    log.info(
        "roh: %d works read, %d stubs skipped; %d productions and %d unlinked performances queued",
        len(sweep.works),
        sweep.stubs,
        len(sweep.productions),
        len(sweep.performances),
    )


def _work_document(work: WorkPage, entry: IndexEntry, ingested_at: datetime) -> WorkMentionDocument:
    """The work itself, paired with its composer.

    ``title`` is the composer's title for the music where the page gives one and
    the staged title otherwise (:attr:`~.works.WorkPage.work_title`); ``composer``
    is the first of the credited composers, with the rest in ``raw`` — the
    document type carries one, and a co-composed work like the Rimsky-Korsakov
    *Boris Godunov* must not be dropped for having two.
    """
    return WorkMentionDocument(
        id=external_id("work", work.work_id),
        url=work_url(work.work_id),
        source_name=RohAdapter.name,
        ingested_at=ingested_at,
        title=work.work_title,
        composer=work.composers[0] if work.composers else None,
        raw={
            "work_id": work.work_id,
            "genre": work.genre or entry.genre,
            "staged_title": work.title,
            "music_title": work.music_title,
            "composers": list(work.composers),
            "credits": _credit_list(work.credits),
            "fields": dict(work.fields),
            "premieres": _premieres(work),
            "productions": [
                {
                    "production_id": ref.production_id,
                    "title": ref.title,
                    "companies": ref.companies,
                    "performances": ref.performances,
                }
                for ref in work.productions
            ],
            "unlinked_performances": len(work.unlinked),
            "collections": [{"name": name, "objects": count} for name, count in work.collections],
            "index_title": entry.title,
            "index_credited": list(entry.credited),
        },
    )


def _premieres(work: WorkPage) -> dict[str, dict[str, str | None]]:
    """The three premiere fields, split into an ISO date and a place."""
    found: dict[str, dict[str, str | None]] = {}
    for label in ("World premiere", "ROH premiere", "ROH company premiere"):
        stated = work.fields.get(label)
        if stated is None:
            continue
        found[label] = {"stated": stated, "date": iso_date(stated), "place": place(stated)}
    return found


# ---------------------------------------------------------------------------
# Tier 2: productions. No documents of their own — a staging is not a work, and
# nobody on one is a composer — but they hold the credits and the nights.
# ---------------------------------------------------------------------------


def _read_productions(sweep: _Sweep, registry: Registry) -> None:
    read = 0
    for production_id, work_id in sweep.productions:
        if sweep.exhausted:
            break
        page = sweep.fetch(production_url(production_id))
        if page is None:
            continue
        production = parse_production(production_id, work_id, page)
        sweep.unknown |= production.unknown
        read += 1
        _record_credits(
            registry,
            production.credits,
            genre=production.genre,
            work_id=work_id,
            production_id=production_id,
        )
        sweep.performances.extend((work_id, production_id, ref) for ref in production.performances)
        _check_count(production, sweep)
    log.info("roh: %d productions read, %d performances queued", read, len(sweep.performances))


def _check_count(production: ProductionPage, sweep: _Sweep) -> None:
    """Warn when a production yields a different number of nights than advertised.

    The work page states "(34 performances online)" for each of its productions;
    a mismatch means the listing parser has lost rows, which would otherwise show
    up only as a sweep that quietly ran short.
    """
    work = sweep.works.get(production.work_id)
    if work is None:
        return
    for ref in work.productions:
        if ref.production_id == production.production_id and ref.performances is not None:
            if ref.performances != len(production.performances):
                log.warning(
                    "production %d: work page says %d performances, page lists %d",
                    production.production_id,
                    ref.performances,
                    len(production.performances),
                )
            return


# ---------------------------------------------------------------------------
# Tier 3: performances. One mention per night, composer carried down from the
# work — see the module docstring on why it cannot come from the page.
# ---------------------------------------------------------------------------


def _read_performances(
    sweep: _Sweep, registry: Registry, ingested_at: datetime
) -> Iterator[WorkMentionDocument]:
    read = 0
    seen: set[int] = set()
    for work_id, production_id, ref in sweep.performances:
        if sweep.exhausted:
            break
        if ref.performance_id in seen:
            # The two sources of nights are disjoint by construction — the work
            # page's table is headed "not linked to a production" — so this
            # guards the construction rather than a known overlap.
            continue
        seen.add(ref.performance_id)
        page = sweep.fetch(performance_url(ref.performance_id))
        if page is None:
            continue
        performance = parse_performance(ref.performance_id, work_id, production_id, page, ref.date)
        sweep.unknown |= performance.unknown
        read += 1
        _check_parent(performance)
        work = sweep.works.get(work_id)
        _record_performance_credits(registry, performance, ref)
        if work is None:
            # Only reachable if the work tier was cut short by the budget while
            # its children were already queued; the mention would have no
            # composer, which is the one thing this source exists to provide.
            continue
        sweep.mentions += 1
        yield _performance_document(work, performance, ref, ingested_at)
    log.info("roh: %d performances read", read)


def _check_parent(performance: PerformancePage) -> None:
    """Warn when a night's own "List All" link disagrees with the walk that found it.

    The edges between the three levels are stated only downwards — a production
    page names neither its work nor anything above it — so the walk carries the
    parent down and nothing on the page confirms it. Except this: the "List All"
    link points back at whichever level the night hangs from. It is the only
    check available on the one part of this source that is inferred rather than
    read, so a disagreement means the walk has attributed a night to the wrong
    work and every mention from it carries the wrong composer.

    Silent when the page states no parent, which ~1 night in 6 does.
    """
    walked = (
        ("production", performance.production_id)
        if performance.production_id is not None
        else ("work", performance.work_id)
    )
    if performance.parent is not None and performance.parent != walked:
        log.warning(
            "performance %d: walked from %s, page links back to %s",
            performance.performance_id,
            walked,
            performance.parent,
        )


def _performance_document(
    work: WorkPage, performance: PerformancePage, ref: PerformanceRef, ingested_at: datetime
) -> WorkMentionDocument:
    """One night of one work.

    ``title`` is the work's, not the heading's: the evening may have been an
    excerpt ("Aida - Act IV scene 1 …"), and what the composer wrote is the
    work. The performed title rides along in ``raw`` so the distinction is not
    lost.
    """
    return WorkMentionDocument(
        id=external_id("performance", performance.performance_id),
        url=performance_url(performance.performance_id),
        source_name=RohAdapter.name,
        ingested_at=ingested_at,
        title=work.work_title,
        composer=work.composers[0] if work.composers else None,
        raw={
            "performance_id": performance.performance_id,
            "work_id": work.work_id,
            "production_id": performance.production_id,
            "genre": performance.genre or work.genre,
            "date": iso_date(ref.date),
            "date_stated": ref.date,
            "time": ref.time,
            "venue": ref.venue or performance.venue,
            "company": performance.company,
            "performed_title": performance.title,
            "work_title": work.work_title,
            "staged_title": work.title,
            "composers": list(work.composers),
            "conductors": list(performance.conductors),
            "credits": _credit_list(performance.credits),
            "cast": [
                {
                    "role": entry.role,
                    "name": entry.name,
                    "character": entry.character,
                    "note": entry.note,
                }
                for entry in performance.cast
            ],
            "fields": dict(performance.fields),
            "parent": list(performance.parent) if performance.parent else None,
        },
    )


# ---------------------------------------------------------------------------
# Credits into the registry.
# ---------------------------------------------------------------------------


def _credit_list(credits: tuple[Credit, ...]) -> list[dict[str, str | None]]:
    """Credits as raw-serialisable dicts, keeping the label the page printed.

    Both the printed label and the folded role are kept: the role is what the
    profession claim is made from, the label is what the archive actually said,
    and "Revival associate director" is worth not flattening to "director" in
    the record.
    """
    return [
        {"role": credit.role, "label": credit.label, "name": credit.name, "note": credit.note}
        for credit in credits
    ]


def _record_credits(
    registry: Registry,
    credits: tuple[Credit, ...],
    *,
    genre: str | None,
    work_id: int,
    production_id: int | None = None,
) -> None:
    for credit in credits:
        person = registry.credit(credit.name, credit.role, credit.label, genre=genre)
        if person is None:
            continue
        person.works.add(work_id)
        if production_id is not None:
            person.productions.add(production_id)


def _record_performance_credits(
    registry: Registry, performance: PerformancePage, ref: PerformanceRef
) -> None:
    """The pit and the cast of one night.

    Cast rows are credited under the fixed label "Cast" with the part recorded
    separately: the part is a character, not a job, and filing "Don José" as a
    role would put a profession claim on every opera character in the archive.
    See :mod:`.people`.
    """
    date = iso_date(ref.date)
    for credit in performance.credits:
        person = registry.credit(credit.name, credit.role, credit.label, genre=performance.genre)
        _attach(registry, person, performance, date)
    for entry in performance.performers:
        person = registry.credit(entry.name, "cast", "Cast", genre=performance.genre, part=entry.role or None)
        _attach(registry, person, performance, date)


def _attach(
    registry: Registry, person: Person | None, performance: PerformancePage, date: str | None
) -> None:
    if person is None:
        return
    person.works.add(performance.work_id)
    if performance.production_id is not None:
        person.productions.add(performance.production_id)
    person.performances.add(performance.performance_id)
    registry.seen_at(person, date)
