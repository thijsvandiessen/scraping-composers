"""What a crawl is doing while it does it, and the tally it leaves behind.

A crawl of a few thousand pages runs unattended for a long time, so it has to say
where it is rather than going quiet until the manifest is written. This mirrors
:mod:`composer_extract.run` / :class:`~composer_extract.resilience.ExtractStats`
deliberately, so both halves of the pipeline report the same shape: a per-item
DEBUG line, a periodic INFO line, and a closing summary.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .records import CrawlRecord

log = logging.getLogger(__name__)

#: How often a long crawl says where it is; a 5k-page crawl must not be silent.
_PROGRESS_EVERY = 25


@dataclass
class CrawlStats:
    """What a crawl did, so an unattended one can be judged after the fact.

    ``empty`` counts pages that were fetched successfully but carry no markdown:
    they cost a page render and give the extract stage nothing to read, which is
    otherwise invisible until the extract produces no documents.
    """

    pages: int = 0
    skipped: int = 0
    empty: int = 0

    def summary(self) -> str:
        return f"{self.pages} pages, {self.skipped} skipped, {self.empty} without markdown"


@dataclass
class CrawlProgress:
    """Counts and narrates one crawl; *total* is how many URLs were queued."""

    name: str
    total: int
    stats: CrawlStats = field(default_factory=CrawlStats)
    started: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def mark_page(self, record: CrawlRecord) -> None:
        """Count a scraped page and, now and then, say how far along the crawl is."""
        self.stats.pages += 1
        chars = len(record.markdown)
        log.debug(
            "crawl %r: %s -> %d %s, %d chars markdown, depth %d",
            self.name,
            record.final_url,
            record.status_code,
            record.content_type,
            chars,
            record.depth,
        )
        if not chars:
            self.stats.empty += 1
            log.warning("crawl %r: %s has no markdown to extract from", self.name, record.final_url)
        if self.stats.pages % _PROGRESS_EVERY == 0:
            log.info(
                "crawl %r: %d/%d pages in %.0fs (%s)",
                self.name,
                self.stats.pages,
                self.total,
                self.elapsed,
                self.stats.summary(),
            )

    def mark_skipped(self, url: str) -> None:
        """Count a URL that produced no record; ``record_from_result`` logged why."""
        self.stats.skipped += 1

    def finish(self) -> None:
        log.info("crawl %r finished in %.0fs: %s", self.name, self.elapsed, self.stats.summary())
