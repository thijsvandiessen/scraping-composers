"""Parsing for classicfm.com's composer and artist name-index pages.

Both /composers/ and /artists/ publish identical "grouped_links" markup: an
A-Z index with one ``<li><a class="grouped_links__list__link">`` per name.
Each page also repeats a handful of the same names in a separate "Featured
composers"/"Featured artists" card carousel, but those anchors carry no
``class`` attribute at all, so keying the regex on ``grouped_links__list__link``
finds every name in the full index and skips the featured/nav/footer
duplicates for free — no need to first isolate the index's containing div.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

_ENTRY_RE = re.compile(
    r'<a href="(?P<path>/(?:composers|artists)/[^"]+?/)"'
    r' class="grouped_links__list__link">(?P<name>[^<]*)</a>'
)


@dataclass(frozen=True)
class ClassicFmEntry:
    path: str
    name: str


def parse_entries(page_html: str) -> list[ClassicFmEntry]:
    """Every (path, name) pair from a composers/artists index page.

    Names are HTML-unescaped and whitespace-stripped (some entries carry
    incidental trailing spaces in the source markup, e.g. "Craig Ogden  ").
    Entries are deduplicated by path, preserving first-seen order.
    """
    seen: set[str] = set()
    entries: list[ClassicFmEntry] = []
    for match in _ENTRY_RE.finditer(page_html):
        path = match.group("path")
        if path in seen:
            continue
        name = html.unescape(match.group("name")).strip()
        if not name:
            continue
        seen.add(path)
        entries.append(ClassicFmEntry(path=path, name=name))
    return entries
