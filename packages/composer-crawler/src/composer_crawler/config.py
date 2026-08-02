"""Crawl configuration: what to discover and how to rank it for scraping.

A :class:`CrawlConfig` declares seed URLs plus discovery and relevance-ranking
rules; the :class:`~composer_crawler.crawler.Crawler` executes it with crawl4ai
without knowing anything about the target site. Discovery finds URLs (primarily
from ``sitemap.xml``); ranking orders them by relevance to ``relevance_query``
so the most important pages are scraped first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: A crawl name is ours to constrain, so it is an allowlist rather than a list of
#: things to reject. It keeps the name a single path segment (CWE-22: ``name``
#: becomes a directory under the bucket root) and, because the name travels from
#: there into log lines and terminals, keeps control characters out of it — a
#: newline in a name is enough to forge a log entry (CWE-117).
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

#: The LLM schemas ``extract`` can apply to a crawl's pages. The names live here,
#: with the config they validate, rather than in :mod:`composer_extract` — which
#: depends on this package, not the other way round. ``composer_extract.registry``
#: holds the entry point behind each name and checks the two stay in step.
#:
#: "concerts" and "recordings" each look for one shape and ignore pages that do
#: not have it; "claims" records whatever a page states, so it is normally enabled
#: *alongside* one of the others rather than instead of it.
EXTRACT_KINDS: tuple[str, ...] = ("concerts", "recordings", "claims")

#: What a config gets when it names no kind.
DEFAULT_EXTRACT_KIND = "concerts"


def _validate_source_name(value: str) -> None:
    """Require a plain, single-path-segment crawl name.

    Rejected before a crawl starts rather than when it first writes, so a name
    that could escape the bucket or forge a log line never reaches either.
    """
    if value in (".", "..") or not _SAFE_NAME.match(value):
        raise ValueError(
            f"invalid crawl name {value!r}: must be a single path segment "
            "of letters, digits, dot, dash or underscore"
        )


@dataclass(frozen=True)
class CrawlConfig:
    """Declarative description of one crawl target.

    The crawl runs in two phases: *discovery* collects candidate URLs (from the
    site's ``sitemap.xml`` by default, optionally Common Crawl), and *scraping*
    fetches them, rendered in a headless browser, in descending order of
    relevance to ``relevance_query``. When no query is set, URLs keep their
    discovery order.
    """

    name: str
    seeds: tuple[str, ...]
    # Discovery: sitemap.xml (and optionally Common Crawl) seeded from each seed's host.
    use_sitemap: bool = True
    use_common_crawl: bool = False
    # A glob (crawl4ai URL-seeder syntax) restricting which discovered URLs are kept.
    allow_patterns: tuple[str, ...] = ()
    # Ranking: BM25 over each URL's head metadata against this query; None keeps discovery order.
    relevance_query: str | None = None
    score_threshold: float = 0.0
    # When discovery yields nothing, follow links from the seeds up to this depth instead.
    follow_links: bool = False
    max_depth: int = 2
    # Cap on the number of URLs scraped (None: no cap).
    max_pages: int | None = None
    # CSS selector for elements dropped before markdown generation, added to the
    # consent-dialog selector every crawl already applies. Consent banners are dense
    # prose that survives the pruning filter and can dwarf a page's real content.
    excluded_selector: str | None = None
    request_delay_s: float = 0.5
    headers: tuple[tuple[str, str], ...] = ()
    respect_robots: bool = True
    timeout_s: float = 30.0
    # Which LLM schemas the `extract` step applies to this crawl's pages. Each
    # named kind runs over every page, so a site whose pages carry both a
    # programme and other stated facts declares ("concerts", "claims").
    extract_kinds: tuple[str, ...] = (DEFAULT_EXTRACT_KIND,)

    def __post_init__(self) -> None:
        _validate_source_name(self.name)
        if not self.seeds:
            raise ValueError(f"crawl {self.name!r}: seeds must not be empty")
        if not self.extract_kinds:
            raise ValueError(f"crawl {self.name!r}: extract_kinds must name at least one kind")
        if len(set(self.extract_kinds)) != len(self.extract_kinds):
            # Each kind is one LLM call per page; a repeat is silently paid for twice.
            raise ValueError(f"crawl {self.name!r}: extract_kinds must not repeat a kind")
        for kind in self.extract_kinds:
            if kind not in EXTRACT_KINDS:
                raise ValueError(
                    f"crawl {self.name!r}: extract_kinds must be drawn from "
                    f"{', '.join(repr(k) for k in EXTRACT_KINDS)}, got {kind!r}"
                )
        if self.follow_links and not self.allow_patterns:
            # An unrestricted link-following crawl would wander off the target host.
            raise ValueError(f"crawl {self.name!r}: follow_links requires at least one allow pattern")
        # allow_patterns are globs (crawl4ai URL-seeder / URLPatternFilter syntax), not regexes.
        for pattern in self.allow_patterns:
            if not pattern:
                raise ValueError(f"crawl {self.name!r}: allow patterns must be non-empty globs")
