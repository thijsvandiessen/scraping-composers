"""The performance page — one night, its cast and who was in the pit.

``performance.aspx?performance=<id>`` is three blocks: a detail table with the
company, venue and the credits that belong to the evening rather than the
staging (conductor, chorus master, leader); a cast table; and a second detail
table of everything about the occasion — language, durations, broadcasts, and
the archival document the record was catalogued from.

**Nothing here names a composer**, which is the fact that shapes this whole
source. Reading the paginated performance search for opera end to end — 285
pages, 5,690 records — yields not one. The composer is on the work page three
levels up, and a performance is only meaningful once it is read as a descendant
of one.

**The date is not parsed from this page.** The heading welds the title to the
date with a hyphen and no separator — ``Aida - Act IV scene 1 'L'abborrita
rivale a me sfuggia'-28 June 1988 Evening`` — and titles here contain hyphens
freely. Since a performance is only ever reached from a listing that already
states its date, time and venue (:class:`~.listings.PerformanceRef`), that date
is passed *in* and used to split the heading, which turns an ambiguous parse
into an exact one: everything before the last occurrence of the known date is
the title.

**A cast row need not name anyone.** The table doubles as the programme's
running order, so "ACT II" and "Pas de huit" appear in the role column with an
empty name cell, as do un-cast parts and ensemble entries. They are kept —
dropping them would lose the shape of the evening — and :attr:`CastEntry.name`
is empty for them, which is what the adapter checks before making anyone a
person.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .credits import Credit, read, unknown_labels
from .text import body, cells, row_html, table, tables, text
from .urls import page_ref

#: Labels on a performance page that credit a person — the musicians of the
#: evening rather than the staging. An allowlist, as at the other two levels and
#: for the same reason (see :mod:`.credits`): this page carries more stated
#: facts than either of them, and treating an unrecognised one as a credit would
#: file a broadcast date or a running time as a person.
ROLES = frozenset(
    {
        "chorus_master",
        "concert_master",
        "conductor",
        "leader",
        "music_director",
        "piano_conductor",
        "surtitled",
        "translator",
    }
)

#: Labels on a performance page that state a fact rather than credit a person.
#: This tier states more facts than either of the others — what was broadcast,
#: how long it ran, which archival document the record came from — which is why
#: the credit half is the one enumerated.
FIELDS = frozenset(
    {
        "big_screen_live_relay",
        "big_screen_relay_to",
        "commercial_recording",
        "company",
        "durations",
        "event",
        "information_source",
        "language",
        "notes",
        "performance_status",
        # The broadcast rows are a family: television, radio and big-screen,
        # each in a live and a future form. An unmet member is reported, not
        # lost — see :mod:`.credits` on what a field label does and does not do.
        "radio_future_relay",
        "radio_live_relay",
        "television_future_relay",
        "television_live_relay",
        "venue",
    }
)

#: Rows naming people that are not a performance credit — see
#: :data:`~.productions.NOT_CREDITS`.
NOT_CREDITS = frozenset({"sponsor", "supported"})

_TITLE_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1\s*>", re.DOTALL | re.IGNORECASE)
_HEADING_RE = re.compile(r"<h2\b[^>]*>(.*?)</h2\s*>", re.DOTALL | re.IGNORECASE)
_DETAILS_RE = re.compile(r"^(.*?):\s*performance details$", re.IGNORECASE)
_PARENT_RE = re.compile(r"""uiPerfLinkAll["'][^>]*\bhref\s*=\s*["']?([^"'\s>]+)""", re.IGNORECASE)
_ITALIC_RE = re.compile(r"<i\b[^>]*>(.*?)</i\s*>", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class CastEntry:
    """One line of the cast list.

    ``role`` is the part ("Carmen", "Tavern Dancer") or, on the running-order
    rows, an act marker; ``character`` is the italic gloss the site sometimes
    adds ("a nymph and demi-goddess"); ``name`` is the performer, and is empty
    on the rows that name no one — see the module docstring.
    """

    role: str
    name: str
    character: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class PerformancePage:
    """One night, as its own page states it."""

    performance_id: int
    #: The work and production this performance belongs to, from the listing
    #: that led here. ``production_id`` is None for the performances that hang
    #: off a work directly.
    work_id: int
    production_id: int | None
    title: str
    genre: str | None
    credits: tuple[Credit, ...] = ()
    cast: tuple[CastEntry, ...] = ()
    fields: dict[str, str] = field(default_factory=dict)
    #: What the page's own "List All" link points at — a production or a work.
    #: Read as a cross-check on the walk, never as the route to this page.
    parent: tuple[str, int] | None = None
    unknown: frozenset[str] = frozenset()

    @property
    def company(self) -> str | None:
        return self.fields.get("Company")

    @property
    def venue(self) -> str | None:
        return self.fields.get("Venue")

    @property
    def conductors(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.credits if c.role == "conductor")

    @property
    def performers(self) -> tuple[CastEntry, ...]:
        """The cast rows that actually name someone."""
        return tuple(entry for entry in self.cast if entry.name)


def parse(
    performance_id: int, work_id: int, production_id: int | None, page: str, date: str = ""
) -> PerformancePage:
    """Read ``performance.aspx?performance=<performance_id>``.

    *date* is the date the parent listing gave for this performance; it is used
    only to split the title out of the heading (see the module docstring) and
    may be omitted, in which case the heading is kept whole.
    """
    panel = body(page)
    detail = "".join(tables(panel, "result", without="work"))
    occasion = "".join(tables(panel, "result", "work"))
    credits, fields = read(detail + occasion, ROLES)
    return PerformancePage(
        performance_id=performance_id,
        work_id=work_id,
        production_id=production_id,
        title=_title(panel, date),
        genre=_genre(panel),
        credits=credits,
        cast=_cast(panel),
        fields=fields,
        parent=_parent(panel),
        unknown=frozenset(unknown_labels(detail + occasion, ROLES, FIELDS | NOT_CREDITS)),
    )


def _title(panel: str, date: str) -> str:
    """The performed title, cut off the heading at the date the listing gave.

    Falls back to the whole heading when the date is unknown or absent from it —
    better a title with a date stuck to it than a title truncated at the wrong
    hyphen.
    """
    match = _TITLE_RE.search(panel)
    if match is None:
        return ""
    heading = text(match.group(1))
    if not date:
        return heading
    cut = heading.rfind(date)
    return heading[:cut].rstrip(" -–") if cut > 0 else heading


def _genre(panel: str) -> str | None:
    for heading in _HEADING_RE.findall(panel):
        match = _DETAILS_RE.match(text(heading))
        if match is not None:
            return match.group(1).strip()
    return None


def _parent(panel: str) -> tuple[str, int] | None:
    match = _PARENT_RE.search(panel)
    return page_ref(match.group(1)) if match else None


def _cast(panel: str) -> tuple[CastEntry, ...]:
    """The cast list, in the order the programme printed it.

    The cast table shares its classes with the listing tables on the pages
    above, but has three positional columns rather than a link: part, a note
    column that is almost always empty, and the performer.
    """
    fragment = table(panel, "results", "performance")
    if fragment is None:
        return ()
    entries: list[CastEntry] = []
    for raw in row_html(fragment):
        columns = cells(raw)
        if len(columns) < 3:
            continue
        italic = _ITALIC_RE.search(columns[0])
        entries.append(
            CastEntry(
                role=text(_ITALIC_RE.sub("", columns[0])),
                name=text(columns[2]),
                character=text(italic.group(1)) or None if italic else None,
                note=text(columns[1]) or None,
            )
        )
    return tuple(entries)
