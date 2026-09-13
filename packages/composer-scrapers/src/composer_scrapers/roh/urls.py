"""Canonical rohcollections.org.uk URLs, and the four page kinds this source reads.

The Royal Opera House performance database is a four-level hierarchy, and each
level is one ``.aspx`` page keyed by an integer id:

``performanceindex.aspx?genre=&letter=``
    The enumerator. One page per initial letter, listing every *work* the
    database holds with its genre and title. No pagination at any letter.
``work.aspx?work=<id>``
    A work — **the only level that names a composer.**
``production.aspx?production=<id>``
    One staging of that work, with its design and direction credits.
``performance.aspx?performance=<id>``
    One night, with cast, conductor and venue.

Two things about this scheme are load-bearing.

**The ids are the identity; the query strings around them are not.** Every link
the site writes carries navigation state that has nothing to do with the record
— ``performance.aspx?performance=9063&row=1&searchtype=performance&genre=Opera&page=0``
is the same performance as ``performance.aspx?performance=9063``, reached from a
different search. Left alone, that state would mirror one page under as many
URLs as there are paths to it and give one record as many external ids. So
:func:`page_ref` reads the id out of an href and throws the rest away, and every
URL this module builds carries the id parameter and nothing else.

**Case varies between link sites.** The same page is linked as
``performance.aspx``, ``Performance.aspx`` and ``performance.ASPX`` depending on
which template wrote the link — the "List All" back-link on a performance page
says ``Work.aspx?work=551`` while the index says ``work.aspx?work=9``. ASP.NET
does not care; a case-sensitive parser does, so matching here is
case-insensitive and the canonical form this module emits is lower-case.
"""

from __future__ import annotations

import html
import re

BASE_URL = "https://www.rohcollections.org.uk"

#: The genre facet used to enumerate. ``All`` is one of the site's own options
#: and returns every genre in one sweep, with each row still labelled — so the
#: 27 letter pages cover the whole database rather than 27 per genre.
INDEX_GENRE = "All"

#: Every initial the index offers, in the site's own order. ``0-9`` is a bucket,
#: not a letter, and ``X`` is currently empty but is still requested: an index
#: that grows an X-title should not need a code change to find it.
LETTERS = ("0-9", *(chr(c) for c in range(ord("A"), ord("Z") + 1)))

#: Query parameter -> record kind, for the three detail pages.
_PARAMS = {"work": "work", "production": "production", "performance": "performance"}

_REF_RE = re.compile(
    r"(?:^|/)(work|production|performance)\.aspx\?(?:[^#]*&)?(work|production|performance)=(\d+)\b",
    re.IGNORECASE,
)


def index_url(letter: str, genre: str = INDEX_GENRE) -> str:
    """The browse-by-title page for one initial.

    ``genre`` is left settable because the site offers the facet and a caller
    may want one slice of it, but the sweep uses :data:`INDEX_GENRE`.
    """
    return f"{BASE_URL}/performanceindex.aspx?genre={genre}&letter={letter}"


def detail_url(kind: str, record_id: int) -> str:
    """The canonical URL for one work, production or performance.

    Carries the id parameter alone — see the module docstring on why the
    navigation state the site hangs off its own links is dropped here.
    """
    if kind not in _PARAMS:
        raise ValueError(f"unknown record kind: {kind!r}")
    return f"{BASE_URL}/{kind}.aspx?{kind}={record_id}"


def work_url(work_id: int) -> str:
    return detail_url("work", work_id)


def production_url(production_id: int) -> str:
    return detail_url("production", production_id)


def performance_url(performance_id: int) -> str:
    return detail_url("performance", performance_id)


def external_id(kind: str, record_id: int) -> str:
    """The source-local id for a record — ``work/551``, ``performance/18316``.

    Deliberately not the URL: the id survives the site moving off ``.aspx``,
    and it is what a document's ``id`` is built from.
    """
    if kind not in _PARAMS:
        raise ValueError(f"unknown record kind: {kind!r}")
    return f"{kind}/{record_id}"


def page_ref(href: str) -> tuple[str, int] | None:
    """``(kind, id)`` for a work, production or performance link, else None.

    None for the index, the search pages, ``relatedobjects.aspx`` and every
    off-site link. Entities are resolved first, so an href lifted straight out
    of the markup works: the site writes its own links with ``&amp;``
    separators, and ``performance.aspx?row=1&amp;page=0&amp;performance=17234``
    must not read as a page with no id. The page name and the id parameter must
    agree —
    ``work.aspx?work=551`` matches, a hypothetical ``work.aspx?production=3775``
    does not — because a mismatch means the pattern has caught something this
    parser does not understand, and guessing which half to believe is how a
    walk ends up mirroring production pages under work ids.
    """
    match = _REF_RE.search(html.unescape(href).strip())
    if match is None:
        return None
    page, param, record_id = match.group(1).lower(), match.group(2).lower(), match.group(3)
    if page != param:
        return None
    return _PARAMS[page], int(record_id)
