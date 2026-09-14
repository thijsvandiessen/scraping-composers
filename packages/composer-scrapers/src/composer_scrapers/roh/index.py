"""The browse-by-title index — the enumerator for the whole performance database.

``performanceindex.aspx?genre=All&letter=<L>`` lists every work whose title
begins with one letter, as ``<td class="genre">`` and ``<td class="title">``
holding a link to the work and, usually, a name in brackets after it. Twenty-
seven such pages cover the database: there is no pagination at any letter, and
requesting ``genre=All`` keeps the per-row genre label, so one 27-page sweep
replaces eleven.

This is worth stating because the obvious enumerator is the wrong one. The
paginated performance search — ``SearchResults.aspx?searchtype=performance&
genre=Opera`` — is 285 pages for opera and another 488 for ballet (5,690 and
9,757 performances, twenty to a page), and what it lists is *performances*, none
of which name a composer. The index is 27 pages and reaches every composer the
database holds.

**The bracketed name is not a composer.** For an opera it is; for a ballet it is
the *choreographer* — "A new pas de deux (Kenneth MacMillan)", whose composer is
Stravinsky — and the index gives no hint which convention is in force beyond the
genre. It is parsed here because it is a free cross-check on the work page and
belongs in the raw record, and it is **never** read as a composer: that comes
from the work page, where the row is labelled.

**Some rows are not works.** 47 of the 995 are cross-reference stubs — "The
Barber of Seville: see 'Il barbiere di Siviglia' (under B in Search by Title)" —
pointing at the entry that holds the real record. They are left in the listing
rather than filtered on their title, whose wording varies too much to match on
("see", "See also", ": see", "- See"); the work page says plainly what they are,
by carrying no details at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .text import text
from .urls import page_ref

#: One index row: the genre cell, then the title cell with its link and the
#: bracketed name trailing it. Anchored on the two class names because the page
#: also carries the site navigation's own tables.
_ROW_RE = re.compile(
    r'<td\s+class="genre">(.*?)</td>\s*<td\s+class="title">(.*?)</td>',
    re.DOTALL | re.IGNORECASE,
)
_LINK_RE = re.compile(
    r"""<a\b[^>]*\bhref\s*=\s*["']?([^"'\s>]+)["']?[^>]*>(.*?)</a\s*>""", re.DOTALL | re.IGNORECASE
)
_BRACKETED_RE = re.compile(r"\(([^()]*)\)\s*$")


@dataclass(frozen=True)
class IndexEntry:
    """One work as the index lists it, before its own page has been read."""

    work_id: int
    genre: str
    title: str
    #: The bracketed names, split on commas — composers for an opera,
    #: choreographers for a ballet. Kept for the raw record and as a
    #: cross-check; never used as a composer. See the module docstring.
    credited: tuple[str, ...]


def entries(page: str) -> list[IndexEntry]:
    """Every work one letter of the index lists, in the site's own order.

    Rows whose link is not a work link are skipped: the title cell of a
    cross-reference occasionally points at a production instead, and a
    production id read as a work id would fetch the wrong page under the right
    name.
    """
    found: list[IndexEntry] = []
    for genre_cell, title_cell in _ROW_RE.findall(page):
        link = _LINK_RE.search(title_cell)
        if link is None:
            continue
        ref = page_ref(link.group(1))
        if ref is None or ref[0] != "work":
            continue
        found.append(
            IndexEntry(
                work_id=ref[1],
                genre=text(genre_cell),
                title=text(link.group(2)),
                credited=_credited(title_cell[link.end() :]),
            )
        )
    return found


def _credited(trailing: str) -> tuple[str, ...]:
    """The bracketed names following a title, split on commas.

    Only a trailing bracket counts. Titles carry brackets of their own —
    "La Bayadère (1941)", "Boris Godunov [1896]" — but those sit inside the
    link, which the caller has already cut away.
    """
    match = _BRACKETED_RE.search(text(trailing))
    if match is None:
        return ()
    return tuple(name for name in (part.strip() for part in match.group(1).split(",")) if name)
