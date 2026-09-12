"""Canonical deccaclassics.com URLs, and the two sitemap kinds this source reads.

The site is bilingual and its sitemap is almost entirely German: of the 3575
catalogue entries it lists, 3575 are ``/de/katalog/produkte/<slug>`` and exactly
one is the English ``/en/catalogue/`` landing page. The English product pages
exist — they are simply not indexed. The two spellings share a slug, so the
German sitemap doubles as the English inventory once the path is swapped.

Every catalogue slug ends in the product family's numeric id
(``verdi-un-ballo-in-maschera-karajan-7`` -> 7), which is what the GraphQL API
keys on. All 3575 parse, and all 3575 are distinct, so the sitemap alone is a
complete enumeration and nothing here needs to walk the site.
"""

from __future__ import annotations

import re

BASE_URL = "https://www.deccaclassics.com"

#: What the adapter reports as its base, and what the site calls its English home.
SITE_URL = f"{BASE_URL}/en"

SITEMAP_URL = f"{BASE_URL}/sitemap/www.deccaclassics.com_sitemap.xml"

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")

#: ``/de/katalog/produkte/<slug>`` and its English twin. The catalogue is indexed
#: only in German, but both are matched so a future English sitemap needs no change.
_PRODUCT_RE = re.compile(r"^/(?:de/katalog/produkte|en/catalogue/products)/([^/]+)$")

#: The roster pages. German uses two spellings, one of them the pre-rename legacy.
_ARTIST_RE = re.compile(r"^/(?:de/kuenstler-innen|de/kuenstler|en/artists)/([^/]+)$")

#: The family id the catalogue slug ends with.
_FAMILY_ID_RE = re.compile(r"-(\d+)$")


def locations(sitemap_xml: str) -> list[str]:
    """Every ``<loc>`` in a sitemap index or urlset, in document order."""
    return _LOC_RE.findall(sitemap_xml)


def _path(url: str) -> str | None:
    """*url* as a site-relative path, or None when it points somewhere else."""
    for prefix in (f"{BASE_URL}/", "https://deccaclassics.com/", "http://www.deccaclassics.com/"):
        if url.startswith(prefix):
            return "/" + url[len(prefix) :].split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return None


def families(sitemap_xml: str) -> dict[int, str]:
    """Family id -> catalogue slug for every product a urlset lists.

    Both halves are load-bearing and only the sitemap has them together: the id
    is the only key the GraphQL API accepts, while the slug is what builds the
    page URL the documents point a reader at. Insertion order is the sitemap's.

    A slug that does not end in an id is skipped rather than guessed at — there
    is nothing to fall back to.
    """
    found: dict[int, str] = {}
    for url in locations(sitemap_xml):
        path = _path(url)
        if path is None:
            continue
        match = _PRODUCT_RE.match(path)
        if match is None:
            continue
        slug = match.group(1)
        id_match = _FAMILY_ID_RE.search(slug)
        if id_match is None:
            continue
        found.setdefault(int(id_match.group(1)), slug)
    return found


def artist_slugs(sitemap_xml: str) -> list[str]:
    """The roster slugs a urlset lists, deduplicated, in document order.

    The German and English spellings of one artist share a slug, so the same
    artist listed under both collapses to a single entry here.
    """
    found: list[str] = []
    seen: set[str] = set()
    for url in locations(sitemap_xml):
        path = _path(url)
        if path is None:
            continue
        match = _ARTIST_RE.match(path)
        if match is not None and match.group(1) not in seen:
            seen.add(match.group(1))
            found.append(match.group(1))
    return found


def product_url(slug: str) -> str:
    """The English catalogue page for a product family slug."""
    return f"{BASE_URL}/en/catalogue/products/{slug}"


def artist_url(slug: str) -> str:
    """The English roster page for an artist slug."""
    return f"{BASE_URL}/en/artists/{slug}"
