"""Link discovery across boosey.com listing pages.

Work detail URLs have a fixed, publisher-stable shape — ``/cr/music/<slug>/<id>``
— so discovery keys on *that shape* rather than on the markup of whatever page
the links appear in. One function therefore serves the composer index, a
composer's work list and a catalogue search result alike, and survives a
redesign of any of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: ``/cr/music/Walter-Steffens-Kerori/27637``. The trailing integer is the work's
#: stable source-local id; the slug ahead of it is decorative (it changes when a
#: work is retitled or translated), so it is never used as an identifier.
_WORK_HREF = re.compile(
    r"""href=["'](?:https?://(?:www\.)?boosey\.com)?(/cr/music/[^"'/?#]+/(\d+))""",
    re.IGNORECASE,
)

#: ``/composer/Walter+Steffens`` — the composer index and every catalogue page
#: link to composers this way.
_COMPOSER_HREF = re.compile(
    r"""href=["'](?:https?://(?:www\.)?boosey\.com)?(/composer/[^"'?#]+)["']""",
    re.IGNORECASE,
)

#: Standard pagination hint. Boosey's listings may or may not emit it; when they
#: don't, a work list is treated as a single page (see ``fetch.iter_work_links``).
_NEXT_HREF = re.compile(
    r"""<a\b[^>]*\brel=["']next["'][^>]*\bhref=["']([^"']+)["']"""
    r"""|<a\b[^>]*\bhref=["']([^"']+)["'][^>]*\brel=["']next["']""",
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


def next_page_path(html: str) -> str | None:
    """The ``rel="next"`` link on a paginated listing, or ``None`` when last."""
    match = _NEXT_HREF.search(html)
    if match is None:
        return None
    return match.group(1) or match.group(2)
