"""Canonical laphil.com URLs, and the two page kinds this source walks.

The site links to the same page three ways — ``/people/x``,
``https://laphil.com/people/x`` and ``https://www.laphil.com/people/x`` — and
hangs query strings and fragments off the result. Everything here funnels those
into one spelling so the crawl's visited-set, the page mirror and the entity ids
all agree on what "the same page" means.
"""

from __future__ import annotations

import re

BASE_URL = "https://www.laphil.com"

#: ``/events/<slug>`` and ``/people/<slug>`` only. ``/events/instances/<id>/<date>/<slug>``
#: is a per-performance permalink of a page already reached by its ``/events/<slug>``
#: form, so the single-segment requirement drops it without losing anything.
_PATH_RE = re.compile(r"^/(events|people)/([^/]+)$")

_HREF_RE = re.compile(r'href="([^"]+)"')


def canonical(href: str) -> str | None:
    """*href* as ``https://www.laphil.com/<section>/<slug>``, or None.

    None for anything that is not an event or person page: off-site links,
    deeper paths, and the sections this source does not read.
    """
    url = href.strip()
    if not url:
        return None
    for prefix in (f"{BASE_URL}/", "https://laphil.com/", "http://www.laphil.com/", "http://laphil.com/"):
        if url.startswith(prefix):
            url = "/" + url[len(prefix) :]
            break
    if not url.startswith("/"):
        return None
    path = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    match = _PATH_RE.match(path)
    if match is None:
        return None
    return f"{BASE_URL}{path}"


def links(page_html: str) -> list[str]:
    """Every event and person page *page_html* links to, canonicalized.

    Deduplicated but kept in document order: the walk queues these as it finds
    them, and a page lists what it considers most relevant first.
    """
    found: list[str] = []
    seen: set[str] = set()
    for href in _HREF_RE.findall(page_html):
        url = canonical(href)
        if url is not None and url not in seen:
            seen.add(url)
            found.append(url)
    return found


def section(url: str) -> str | None:
    """``"events"`` or ``"people"`` for a canonical URL, else None."""
    match = _PATH_RE.match(url.removeprefix(BASE_URL))
    return match.group(1) if match else None


def slug(url: str) -> str:
    """The trailing path segment of a canonical URL."""
    return url.rsplit("/", 1)[-1]


def person_url(person_slug: str) -> str:
    return f"{BASE_URL}/people/{person_slug}"
