"""HTTP access to classicapedia.com, mirrored page by page.

The site is a static encyclopedia generated in batches. ``robots.txt`` allows
everything this source reads and sets no crawl-delay, and ``/sitemap.xml`` is an
index naming one sitemap per language. The English one lists every page, grouped
by the first path segment:

- ``/en/b/<slug>.html``: a biography (~22k);
- ``/en/t/``, ``/en/s/``: an opera house, a concert hall (the two halves of the
  ``auditorios.html`` listing);
- ``/en/o/``: an orchestra;
- ``/en/p/``, ``/en/i/``, ``/en/f/``: stories, instruments and musical forms,
  which are prose and not read here.

``assets/indice.js`` is the data behind the site's search box: one row per
biography, and the only place a slug maps to a canonical name without fetching
that person's page. The sitemap and index are enumerators, so they are fetched
live; the pages go through the mirror.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from composer_http import SourceSession

from .people import plain_name

log = logging.getLogger(__name__)

BASE_URL = "https://classicapedia.com"
PAGE_ROOT = f"{BASE_URL}/en"
SITEMAP_URL = f"{BASE_URL}/sitemap-en.xml"
INDEX_URL = f"{PAGE_ROOT}/assets/indice.js"

#: A small personal site on shared hosting, and robots.txt asks for nothing: one
#: request a second keeps a full crawl to about six and a half hours.
REQUEST_DELAY_S = 1.0

BIOGRAPHY = "b"
OPERA_HOUSE = "t"
CONCERT_HALL = "s"
ORCHESTRA = "o"
SECTIONS = (BIOGRAPHY, OPERA_HOUSE, CONCERT_HALL, ORCHESTRA)

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_PAGE_URL = re.compile(r"^https://classicapedia\.com/en/([btso])/([^/\s]+)\.html$")
#: Every profile page has one; an error page served with a 200 does not.
_PAGE_MARKER = '<article class="ficha'
_INDEX_PREFIX = "window.INDICE="
#: A row's fields, as the site's ``buscador.js`` documents them.
_ROW_FIELDS = 7


@dataclass(frozen=True)
class PageRef:
    """A page the sitemap lists."""

    section: str
    slug: str
    url: str


@dataclass(frozen=True)
class IndexEntry:
    """One row of the search index."""

    slug: str
    name: str
    activities: tuple[str, ...]
    nationality: str


def page_refs(sitemap_xml: str) -> list[PageRef]:
    """The pages this source reads, in sitemap order."""
    refs: list[PageRef] = []
    for loc in _LOC.findall(sitemap_xml):
        if (match := _PAGE_URL.match(loc)) is not None:
            refs.append(PageRef(match.group(1), match.group(2), loc))
    return refs


def parse_index(script: str) -> list[IndexEntry]:
    """The rows of ``assets/indice.js``; a row of the wrong shape is skipped.

    Raises :class:`ValueError` when the file is not the index at all, since a
    run without it could not tell two people of one name apart.
    """
    text = script.strip().removesuffix(";")
    if not text.startswith(_INDEX_PREFIX):
        raise ValueError("classicapedia: assets/indice.js does not define window.INDICE")
    rows: object = json.loads(text.removeprefix(_INDEX_PREFIX))
    if not isinstance(rows, list):
        raise ValueError("classicapedia: window.INDICE is not a list")
    entries: list[IndexEntry] = []
    for row in rows:  # pyright: ignore[reportUnknownVariableType]
        entry = _index_entry(row)  # pyright: ignore[reportUnknownArgumentType]
        if entry is not None:
            entries.append(entry)
    return entries


def _index_entry(row: object) -> IndexEntry | None:
    if not isinstance(row, list) or len(row) < _ROW_FIELDS:  # pyright: ignore[reportUnknownArgumentType]
        return None
    fields: list[object] = list(row)  # pyright: ignore[reportUnknownArgumentType]
    slug, name, activities, nationality = fields[0], fields[1], fields[4], fields[5]
    if not isinstance(slug, str) or not isinstance(name, str) or not slug or not name:
        return None
    listed: list[object] = list(activities) if isinstance(activities, list) else []  # pyright: ignore[reportUnknownArgumentType]
    return IndexEntry(
        slug=slug,
        name=plain_name(name),
        activities=tuple(activity for activity in listed if isinstance(activity, str)),
        nationality=nationality if isinstance(nationality, str) else "",
    )


def fetch_sitemap(session: SourceSession) -> str:
    return session.get_text(SITEMAP_URL, label="sitemap")


def fetch_index(session: SourceSession) -> str:
    return session.get_text(INDEX_URL, label="search index")


def _is_profile(body: str) -> bool:
    return _PAGE_MARKER in body


def fetch_page(session: SourceSession, url: str) -> str | None:
    """One profile page, from the mirror when it holds it.

    Returns ``None`` rather than raising when the page cannot be fetched or is
    not a profile: a sweep of 22k pages must not be lost to one. Only a profile
    is mirrored, so a failure is retried on the next run.
    """
    body = session.try_get_text(url, label=url, mirror=True, valid=_is_profile)
    if body is None:
        return None
    if not _is_profile(body):
        log.warning("classicapedia: %s is not a profile page", url)
        return None
    return body


def iter_pages(
    session: SourceSession, refs: Iterable[PageRef], max_pages: int | None = None
) -> Iterator[tuple[PageRef, str]]:
    """``(ref, html)`` for each page that answers; ``max_pages`` caps how many
    are requested."""
    for requested, ref in enumerate(refs):
        if max_pages is not None and requested >= max_pages:
            return
        if (body := fetch_page(session, ref.url)) is not None:
            yield ref, body
