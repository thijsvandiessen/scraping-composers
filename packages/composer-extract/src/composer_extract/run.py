"""Per-run context for an extract, and the progress it reports as it goes.

An extract over a large crawl runs unattended for hours, so it has to say where
it is and how much it is dropping. :class:`ExtractOptions` is what a caller hands
in and keeps: its :class:`~.resilience.ExtractStats` is filled in as the run
proceeds and is the only account of what the model failed to produce.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from composer_config import settings

from .resilience import ExtractStats

log = logging.getLogger(__name__)

#: How often a long run reports where it is; a 10k-page extract must not be silent.
_PROGRESS_EVERY = 50


@dataclass
class ExtractOptions:
    """Per-run knobs, and the counters the run fills in as it goes.

    Hold on to the instance you pass in: its ``stats`` is how a caller learns how
    many pages were dropped, which is the only signal an unattended run gives.
    """

    max_chars: int | None = None
    now: datetime | None = None
    stats: ExtractStats = field(default_factory=ExtractStats)


@dataclass(frozen=True)
class ExtractRun:
    """The resolved context every page emission needs."""

    source_name: str
    max_chars: int
    now: datetime
    stats: ExtractStats

    @classmethod
    def start(cls, source_name: str, options: ExtractOptions | None) -> ExtractRun:
        opts = options if options is not None else ExtractOptions()
        return cls(
            source_name=source_name,
            max_chars=opts.max_chars if opts.max_chars is not None else settings.extract_max_chars,
            now=opts.now or datetime.now(UTC),
            stats=opts.stats,
        )

    def mark_page(self) -> None:
        """Count a finished page and, now and then, say so."""
        self.stats.pages += 1
        if self.stats.pages % _PROGRESS_EVERY == 0:
            log.info("extract %s: %s", self.source_name, self.stats.summary())

    def finish(self) -> None:
        log.info("extract %s finished: %s", self.source_name, self.stats.summary())
