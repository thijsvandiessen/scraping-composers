"""Per-run context for an extract, and the progress it reports as it goes.

An extract over a large crawl runs unattended for hours, so it has to say where
it is and how much it is dropping. :class:`ExtractOptions` is what a caller hands
in and keeps: its :class:`~.resilience.ExtractStats` is filled in as the run
proceeds and is the only account of what the model failed to produce.
"""

from __future__ import annotations

import logging
import time
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
    started: float = field(default_factory=time.monotonic)

    @classmethod
    def start(cls, source_name: str, options: ExtractOptions | None) -> ExtractRun:
        opts = options if options is not None else ExtractOptions()
        max_chars = opts.max_chars if opts.max_chars is not None else settings.extract_max_chars
        log.info("extract %s: starting (max_chars=%d)", source_name, max_chars)
        return cls(
            source_name=source_name,
            max_chars=max_chars,
            now=opts.now or datetime.now(UTC),
            stats=opts.stats,
        )

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def _rate(self) -> float:
        """Pages per minute so far — what tells you whether an overnight run will
        actually finish. Measured over at least a second, so a run short enough to
        divide by ~zero reports a dull number rather than an absurd one."""
        return self.stats.pages * 60.0 / max(self.elapsed, 1.0)

    def mark_page(self, url: str, documents: int) -> None:
        """Count a finished page and, now and then, say how the run is going."""
        self.stats.pages += 1
        log.debug("extract %s: %s -> %d document(s)", self.source_name, url, documents)
        if self.stats.pages % _PROGRESS_EVERY == 0:
            log.info(
                "extract %s: %s in %.0fs (%.1f pages/min)",
                self.source_name,
                self.stats.summary(),
                self.elapsed,
                self._rate(),
            )

    def finish(self) -> None:
        log.info(
            "extract %s finished in %.0fs: %s (%.1f pages/min)",
            self.source_name,
            self.elapsed,
            self.stats.summary(),
            self._rate(),
        )
