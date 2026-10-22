"""HTTP access to harmoniamundi.com, mirrored page by page.

**robots.txt, and why the REST API is not used.** The site is WordPress, and its
REST API answers ``/wp-json/wp/v2/albums``, ``/artists`` and ``/composers`` with
the complete post list — ids, slugs, modification timestamps, taxonomy terms.
It would be a far cheaper enumerator than the sitemaps. It is also
``Disallow``ed: the site's robots.txt group for ``User-agent: *`` is ``Allow: /``
with four exceptions, and ``/wp-json`` is one of them. So enumeration goes
through ``sitemap_index.xml``, which is allowed and — verified against the
site's own totals — just as complete, and every field is read from the rendered
page. The API is off limits even though nothing would stop a request.

The same file carries ``Content-Signal: search=yes, ai-train=no,
use=reference``, and ``Disallow: /`` for the named AI crawlers (``ClaudeBot``,
``GPTBot``, ``CCBot``, ``Google-Extended`` and others). This ingest is neither:
it builds a reference index of a catalogue — who composed and performed what,
on which release — and trains nothing. Our ``User-Agent`` is
``composer-ingest/0.1`` with a contact address, and falls under ``*``.

**The mirror.** ~2000 pages at 100-150KB each, of which the fields this scraper
reads are a few hundred bytes. Every page fetch goes through the session's page
mirror, so the sweep is paid once per cadence, a run interrupted
halfway resumes where it stopped, and the raw HTML stays on disk — the
tracklist blob is hand-edited free text and the parser for it will improve, so
re-deriving it without re-fetching 2000 pages is worth the disk. Keep that
bargain: parse from the HTML this module returns, do not fetch around it.

The sitemaps are deliberately *not* mirrored. They are how a re-run learns that
the catalogue has grown; served from a mirror they would pin the sweep to the
albums that existed the first time.
"""

from __future__ import annotations

import logging

from composer_http import SourceSession

from .urls import SITEMAP_INDEX_URL

log = logging.getLogger(__name__)

#: Between uncached requests. robots.txt sets no crawl-delay; this is the same
#: politeness rate the other HTML sources here use.
REQUEST_DELAY_S = 0.5


#: Not optional here, and the failure it prevents is silent rather than loud:
#: ``/en/albums/<slug>`` (no trailing slash) answers ``301`` to
#: ``/en/albums/<slug>/``, and a GET only raises on 4xx and 5xx — so with
#: redirects off a near-miss URL returns an *empty body* that parses as "not an
#: album page" instead of an error. Sitemap locs carry the slash and :mod:`.urls`
#: always builds it, making this the second line of defence rather than the first.
FOLLOW_REDIRECTS = True


def fetch_sitemap_index(session: SourceSession) -> str:
    """The sitemap index, which lists the per-section urlsets."""
    return session.get_text(SITEMAP_INDEX_URL, label="sitemap index")


def fetch_urlset(session: SourceSession, url: str) -> str:
    """One section urlset — an album, artist or composer page list."""
    return session.get_text(url, label=url)


def fetch_page(session: SourceSession, url: str) -> str | None:
    """One album, artist or composer page, from the mirror when it holds it.

    Returns ``None`` rather than raising when the page cannot be fetched. The
    sweep visits ~2000 pages and must not be lost to one of them 404ing or the
    connection dropping — which is how the previous LLM crawl of this site died,
    mid-run, more than once.

    A page that could not be fetched is not mirrored, so the next run retries it
    instead of caching the failure.
    """
    return session.try_get_text(url, label=url, mirror=True)
