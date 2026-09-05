"""The sitemap urlset, read for seed URLs.

It is not an inventory: the file is flat and capped at 1000 URLs per section
(no ``sitemap_index.xml``, and the paginated spellings all return the same
document), while the site has more events and people than that. It is a good
*seed* — the walk in :mod:`composer_scrapers.laphil` reaches the rest through
the links between event and person pages.
"""

from __future__ import annotations

import re

from .urls import canonical

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")


def seed_urls(sitemap_xml: str) -> list[str]:
    """Every event and person page the sitemap lists, deduplicated, in file order."""
    seen: set[str] = set()
    seeds: list[str] = []
    for loc in _LOC_RE.findall(sitemap_xml):
        url = canonical(loc)
        if url is None or url in seen:
            continue
        seen.add(url)
        seeds.append(url)
    return seeds
