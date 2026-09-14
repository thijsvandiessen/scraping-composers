"""The work page — the only level of the database that names a composer.

``work.aspx?work=<id>`` is one detail table (:mod:`.credits`), the productions
and unlinked performances beneath it (:mod:`.listings`), and a list of the
archival collections holding objects for the work.

Three things this parser decides, none of them obvious from the markup.

**Which title is the composer's.** A ballet's own title is not a work by its
composer: "Adagio Hammerklavier" is Hans van Manen's, and Beethoven wrote the
"Piano Sonata No. 29 in B-flat, Op. 106" the page names on the next row. So when
a ``Music title`` row is present :attr:`WorkPage.music_title` holds it, and it —
not the staged title — is what pairs with the composer — 661 of the 948 works
carry one. The same holds for opera, where the row is usually the same title
with its year ("Aida" / "Aida (1871)"), so nothing is lost by preferring it
there. Getting this backwards is how an
archive of stagings turns into a catalogue of works no composer wrote.

**What a cross-reference stub looks like.** 47 of the index's 995 rows are
pointers at another entry rather than works — "The Barber of Seville: see 'Il barbiere di
Siviglia'". Their pages are real pages with real headings and an *empty* detail
table: no credits, no fields, no productions, no performances.
:attr:`WorkPage.is_stub` is that emptiness, which is a far steadier signal than
the wording of the title, whose phrasing varies across "see", "See also", ": see"
and "- See".

**Where the genre comes from.** The heading reads "Opera: Work details", except
on works that also carry archival objects, where a "Related Records" heading is
emitted first. So the genre is read from the heading ending in "Work details"
rather than from the first one. The index states a genre for every row too; the
adapter falls back to it if a heading cannot be read, and over the works swept so
far the two never disagreed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .credits import Credit, read, unknown_labels
from .listings import PerformanceRef, ProductionRef, performances, productions
from .text import body, table, text

#: Labels on a work page that credit a person. See :mod:`.credits` on why this
#: is an allowlist and how it was built.
ROLES = frozenset(
    {
        "adapted",
        "arranger",
        "author",
        "book",
        "book_and_lyrics",
        "choreographer",
        "composer",
        "devised",
        "devised_and_produced",
        "director",
        "editor",
        "film",
        "librettist",
        "lyricist",
        "lyrics",
        "orchestrator",
        "playwright",
        "poet",
        "recording_artist",
        "reconstruction",
        "scenario",
        "script",
        "sound_designer",
        "staging",
        "translator",
        "writer",
    }
)

#: Labels on a work page that state a fact rather than credit a person. Kept
#: explicit so that a label in neither set is reported rather than guessed at.
FIELDS = frozenset(
    {
        "after",
        "after_based_on",
        "based_on",
        "language",
        "music_title",
        "notes",
        "related_work",
        "roh_company_premiere",
        "roh_premiere",
        "title_notes",
        "work_definition",
        "world_premiere",
    }
)

_HEADING_RE = re.compile(r"<h2\b[^>]*>(.*?)</h2\s*>", re.DOTALL | re.IGNORECASE)
_TITLE_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1\s*>", re.DOTALL | re.IGNORECASE)
_COLLECTION_RE = re.compile(
    r"""<a\b[^>]*\bhref\s*=\s*["']?relatedobjects\.aspx[^"'>]*["']?[^>]*>(.*?)</a\s*>\s*\((\d+)\)""",
    re.DOTALL | re.IGNORECASE,
)
_DETAILS_RE = re.compile(r"^(.*?):\s*work details$", re.IGNORECASE)


@dataclass(frozen=True)
class WorkPage:
    """One work, as its own page states it."""

    work_id: int
    title: str
    genre: str | None
    credits: tuple[Credit, ...] = ()
    fields: dict[str, str] = field(default_factory=dict)
    productions: tuple[ProductionRef, ...] = ()
    #: Performances hanging off the work with no production between — see
    #: :mod:`.listings`. Not the work's performances in general.
    unlinked: tuple[PerformanceRef, ...] = ()
    #: ``(collection name, object count)`` for the archival holdings, e.g.
    #: ("Poster Collection", 71). Recorded, not followed.
    collections: tuple[tuple[str, int], ...] = ()
    #: Labels seen in neither :data:`ROLES` nor :data:`FIELDS`.
    unknown: frozenset[str] = frozenset()

    @property
    def is_stub(self) -> bool:
        """Whether this is a cross-reference pointing at another entry.

        See the module docstring: a stub's page carries a heading and nothing
        else, so it names no one and is not a work to emit.
        """
        return not (self.credits or self.fields or self.productions or self.unlinked)

    @property
    def composers(self) -> tuple[str, ...]:
        """Everyone credited as composer, in the order the page lists them."""
        return tuple(c.name for c in self.credits if c.role == "composer")

    @property
    def music_title(self) -> str | None:
        """The composer's title for the music, when the page distinguishes one."""
        return self.fields.get("Music title")

    @property
    def work_title(self) -> str:
        """The title to pair with the composer — the music's, else the staged one."""
        return self.music_title or self.title


def parse(work_id: int, page: str) -> WorkPage:
    """Read ``work.aspx?work=<work_id>``.

    Never raises on a page it does not recognise: a work with no detail table
    comes back as a stub, which is what the cross-references are and what a
    truncated response would be indistinguishable from anyway.
    """
    panel = body(page)
    detail = table(panel, "result", "work")
    credits, fields = read(detail, ROLES) if detail is not None else ((), {})
    return WorkPage(
        work_id=work_id,
        title=_title(panel),
        genre=_genre(panel),
        credits=credits,
        fields=fields,
        productions=productions(panel),
        unlinked=performances(panel),
        collections=_collections(panel),
        unknown=frozenset(unknown_labels(detail, ROLES, FIELDS)) if detail is not None else frozenset(),
    )


def _title(panel: str) -> str:
    match = _TITLE_RE.search(panel)
    return text(match.group(1)) if match else ""


def _genre(panel: str) -> str | None:
    """The genre from the "<Genre>: Work details" heading, or None if absent.

    Scans every ``<h2>`` rather than taking the first: works with archival
    objects emit a "Related Records" heading ahead of the details one.
    """
    for heading in _HEADING_RE.findall(panel):
        match = _DETAILS_RE.match(text(heading))
        if match is not None:
            return match.group(1).strip()
    return None


def _collections(panel: str) -> tuple[tuple[str, int], ...]:
    return tuple((text(name), int(count)) for name, count in _COLLECTION_RE.findall(panel))
