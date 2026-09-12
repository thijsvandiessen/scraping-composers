"""Canonical harmoniamundi.com URLs, and the three page kinds this source reads.

The site is WordPress behind WPML, so every post exists in three locales at
once: French at the bare path (``/albums/<slug>/``), English under ``/en/`` and
German under ``/de/``. The sitemaps list all three. This source reads the
English set and only the English set — the three are the same release with the
same catalogue number, and taking more than one would load every album three
times under three ids.

Two shapes matter beyond that. The **trailing slash is load-bearing**:
``/en/albums/<slug>`` answers ``301`` to ``/en/albums/<slug>/``, so every URL
built here carries it (see :func:`~composer_scrapers.harmoniamundi.fetch.make_client`
for what happens when it does not). And each section's *landing* page shares
its prefix with the detail pages — ``/en/albums/`` is the album search, not an
album — which is why the slug is required to be a non-empty segment of its own.
"""

from __future__ import annotations

import re

BASE_URL = "https://www.harmoniamundi.com"

#: The English site, and the adapter's ``base_url``.
SITE_URL = f"{BASE_URL}/en"

SITEMAP_INDEX_URL = f"{BASE_URL}/sitemap_index.xml"

#: URL path segment -> the kind of page it holds. The site keeps its original
#: French segment names on the English pages ("artistes", "compositeurs"), which
#: is worth spelling out once here rather than at every call site.
_SECTIONS = {"albums": "album", "artistes": "artist", "compositeurs": "composer"}

_PATH_RE = re.compile(r"^/en/(albums|artistes|compositeurs)/([^/]+)/?$")

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")

#: Child sitemaps worth opening, by filename prefix. ``albums-sitemap`` matches
#: ``albums-sitemap.xml`` through ``albums-sitemap6.xml`` — the set grows with
#: the catalogue, so it must not be enumerated.
_ALBUM_SITEMAP = "albums-sitemap"
_PEOPLE_SITEMAPS = ("artists-sitemap", "composers-sitemap")


def locations(sitemap_xml: str) -> list[str]:
    """Every ``<loc>`` in a sitemap or sitemap index, in document order."""
    return _LOC_RE.findall(sitemap_xml)


def child_sitemaps(index_xml: str) -> tuple[list[str], list[str]]:
    """The album sitemaps and the people sitemaps the index points at.

    Selected by filename because the index says nothing else about them. The
    sections this source does not read — pages, concerts, playlists,
    selections — are dropped here rather than after fetching them.
    """
    albums: list[str] = []
    people: list[str] = []
    for url in locations(index_xml):
        filename = url.rsplit("/", 1)[-1]
        if filename.startswith(_ALBUM_SITEMAP):
            albums.append(url)
        elif filename.startswith(_PEOPLE_SITEMAPS):
            people.append(url)
    return albums, people


def page_ref(href: str) -> tuple[str, str] | None:
    """``(kind, slug)`` for an English album, artist or composer page, else None.

    None for the other locales, the section landing pages, off-site links and
    the sections this source does not read. Accepts absolute and site-relative
    hrefs, and tolerates the query strings and fragments the page hangs off
    them.
    """
    url = href.strip()
    if not url:
        return None
    for prefix in (f"{BASE_URL}/", "https://harmoniamundi.com/", "http://www.harmoniamundi.com/"):
        if url.startswith(prefix):
            url = "/" + url[len(prefix) :]
            break
    if not url.startswith("/"):
        return None
    path = url.split("?", 1)[0].split("#", 1)[0]
    match = _PATH_RE.match(path)
    if match is None:
        return None
    return _SECTIONS[match.group(1)], match.group(2)


def profile_ref(href: str) -> tuple[str, str] | None:
    """``("artist"|"composer", slug)`` for a profile link, else None.

    The narrower form of :func:`page_ref`, used on the credits list: a credit
    either links to someone's own page — which gives the roster a stable key —
    or is a bare name.
    """
    ref = page_ref(href)
    return ref if ref is not None and ref[0] != "album" else None


def _section_urls(sitemap_xml: str, kind: str) -> list[str]:
    """Every English page of one kind the urlset lists, deduplicated."""
    seen: set[str] = set()
    urls: list[str] = []
    for loc in locations(sitemap_xml):
        ref = page_ref(loc)
        if ref is None or ref[0] != kind:
            continue
        url = detail_url(kind, ref[1])
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def album_urls(sitemap_xml: str) -> list[str]:
    """The ``/en/albums/<slug>/`` pages a urlset lists."""
    return _section_urls(sitemap_xml, "album")


def profile_urls(sitemap_xml: str) -> list[str]:
    """The ``/en/artistes/`` and ``/en/compositeurs/`` pages a urlset lists.

    Both kinds come back together because both are read by the same parser and
    the same sweep; each URL still says which it is via :func:`page_ref`.
    """
    return _section_urls(sitemap_xml, "artist") + _section_urls(sitemap_xml, "composer")


def detail_path(kind: str, page_slug: str) -> str:
    """``/artistes/<slug>`` for a ``(kind, slug)`` pair — the source-local id form.

    The locale prefix is left off on purpose: it identifies the translation, not
    the release, and the same record read from the French site should not load as
    a second entity.
    """
    for segment, section in _SECTIONS.items():
        if section == kind:
            return f"/{segment}/{page_slug}"
    raise ValueError(f"unknown page kind: {kind!r}")


def detail_url(kind: str, page_slug: str) -> str:
    """The canonical page URL for a ``(kind, slug)`` pair, trailing slash included."""
    return f"{SITE_URL}{detail_path(kind, page_slug)}/"


def album_url(page_slug: str) -> str:
    return detail_url("album", page_slug)


def artist_url(page_slug: str) -> str:
    return detail_url("artist", page_slug)


def composer_url(page_slug: str) -> str:
    return detail_url("composer", page_slug)


def slug(url: str) -> str:
    """The slug of a canonical page URL."""
    return url.rstrip("/").rsplit("/", 1)[-1]
