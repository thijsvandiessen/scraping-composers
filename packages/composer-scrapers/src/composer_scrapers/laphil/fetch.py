"""HTTP access to laphil.com, mirrored page by page.

The site publishes no complete index. ``sitemap.xml`` is a single flat urlset
capped at 1000 URLs per section (1000 ``/events/``, 1000 ``/people/``, and no
paginated variant answers), the events calendar renders client-side, and
``/search`` returns nothing server-side. What the site does have is a densely
linked graph: every event page lists its artists, every person page lists the
events they appeared in. So the sitemap is a seed, not an inventory, and the
whole ``/events/`` + ``/people/`` subgraph is reached by walking those links.

That is thousands of requests, and each page is ~400KB of mostly navigation
chrome, so every page goes through the session's page mirror: the sweep
is paid once a year (the cadence), a run interrupted halfway resumes where it stopped, and — the
reason it matters beyond this scraper — the *raw HTML* stays on disk. Only the
composer credits are read today; a later pass over concerts, programmed works,
performers or venues re-parses the same mirrored pages without touching the
network. Keep that bargain: parse from HTML this module returns, do not fetch
around it.
"""

from __future__ import annotations

import logging

from composer_http import SourceSession

from .urls import BASE_URL

log = logging.getLogger(__name__)

SITEMAP_URL = f"{BASE_URL}/sitemap.xml"

#: Between uncached requests. robots.txt sets no crawl-delay; this is the same
#: politeness rate the other orchestra archives here use.
REQUEST_DELAY_S = 0.5


#: Not optional here: a *past* event answers ``/events/<slug>`` with a 302 to
#: its per-performance permalink, ``/events/instances/<id>/<date>/<slug>``,
#: which serves the same page including the programme. Without following it
#: the entire archive — everything but the current season — reads as an error.
FOLLOW_REDIRECTS = True


def fetch_sitemap(session: SourceSession) -> str:
    """The sitemap urlset, which seeds the walk. Not mirrored: it is the seed."""
    return session.get_text(SITEMAP_URL, label="sitemap")


def fetch_page(session: SourceSession, url: str) -> str | None:
    """One event or person page, from the mirror when it holds it.

    Returns ``None`` rather than raising when the page cannot be fetched: the
    walk visits thousands of pages, and a sweep must not be lost to one of them
    404ing or the connection dropping (the previous LLM crawl of this site died
    exactly that way, mid-run, after 35k records).
    """
    return session.try_get_text(url, label=url, mirror=True)
