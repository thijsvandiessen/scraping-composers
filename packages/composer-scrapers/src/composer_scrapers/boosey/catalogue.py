"""Link discovery across boosey.com listing pages.

Work detail URLs have a fixed, publisher-stable shape — ``/cr/music/<slug>/<id>``
— so discovery keys on *that shape* rather than on the markup of whatever page
the links appear in. One function therefore serves a composer page and a
catalogue search result alike, and survives a redesign of either.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass

#: ``/cr/music/Walter-Steffens-Kerori/27637``. The trailing integer is the work's
#: stable source-local id; the slug ahead of it is decorative (it changes when a
#: work is retitled or translated), so it is never used as an identifier.
_WORK_HREF = re.compile(
    r"""href=["'](?:https?://(?:www\.)?boosey\.com)?(/cr/music/[^"'/?#]+/(\d+))""",
    re.IGNORECASE,
)

#: ``/composer/Walter+Steffens`` — how the index links a composer with a page of
#: their own.
_COMPOSER_HREF = re.compile(
    r"""href=["'](?:https?://(?:www\.)?boosey\.com)?(/composer/[^"'?#]+)["']""",
    re.IGNORECASE,
)

#: ``composerid=2778`` — how the index links every other composer, and how a
#: composer's page links their catalogue search and their biography.
_COMPOSER_ID = re.compile(r"""[?&;]composerid=(\d+)""", re.IGNORECASE)

#: The ">>" link in a search result's page controls.
_NEXT_HREF = re.compile(
    r"""<a\b[^>]*\bhref=["']([^"']+)["'][^>]*\bclass=["'][^"']*\bpageNext\b""",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WorkLink:
    """One work detail page discovered on a listing page."""

    work_id: str
    path: str


def _dedup(items: list[tuple[str, str]]) -> list[WorkLink]:
    """Preserve first-seen order; a listing page repeats the same work in a
    heading link and a thumbnail link."""
    seen: set[str] = set()
    links: list[WorkLink] = []
    for path, work_id in items:
        if work_id in seen:
            continue
        seen.add(work_id)
        links.append(WorkLink(work_id=work_id, path=path))
    return links


def work_links(html: str) -> list[WorkLink]:
    """Every work detail link on one page, deduplicated by work id."""
    return _dedup([(m.group(1), m.group(2)) for m in _WORK_HREF.finditer(html)])


def composer_paths(html: str) -> list[str]:
    """Every ``/composer/...`` path on one page, in first-seen order."""
    seen: set[str] = set()
    paths: list[str] = []
    for match in _COMPOSER_HREF.finditer(html):
        path = match.group(1)
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def composer_ids(html: str) -> list[str]:
    """Every composer id a page links to, in first-seen order."""
    return list(dict.fromkeys(_COMPOSER_ID.findall(html)))


def next_page_path(html: str) -> str | None:
    """The next-page link of a search result, or ``None`` on the last page."""
    match = _NEXT_HREF.search(html)
    return html_lib.unescape(match.group(1)) if match is not None else None
