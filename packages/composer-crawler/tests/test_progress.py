"""What a running crawl reports about itself.

A crawl runs unattended for a long time, so these assert the lines that are the
only evidence it is still making progress — and the ones that name the pages it
fetched but got nothing usable from.
"""

from __future__ import annotations

import logging

import pytest
from composer_crawler import CrawlProgress, CrawlRecord

_LOGGER = "composer_crawler.progress"


def _record(url: str = "https://example.org/a", markdown: str = "# Concert") -> CrawlRecord:
    return CrawlRecord(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        fetched_at="2026-07-27T12:00:00+00:00",
        depth=0,
        headers={},
        markdown=markdown,
    )


def test_every_page_is_reported_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    progress = CrawlProgress("site", total=1)

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        progress.mark_page(_record())

    messages = [r.getMessage() for r in caplog.records]
    assert any("https://example.org/a" in m and "200" in m for m in messages)


def test_progress_is_reported_periodically(caplog: pytest.LogCaptureFixture) -> None:
    """A silent crawl is indistinguishable from a wedged one, so a long run has to
    say where it is without waiting for the end."""
    progress = CrawlProgress("site", total=60)

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        for i in range(50):
            progress.mark_page(_record(f"https://example.org/{i}"))

    info = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert len(info) == 2, "one line every 25 pages"
    assert "25/60 pages" in info[0]
    assert "50/60 pages" in info[1]


def test_a_page_without_markdown_is_warned_about(caplog: pytest.LogCaptureFixture) -> None:
    """A successfully fetched page with no markdown costs a render and gives the
    extract stage nothing; without this it is invisible until no documents come out."""
    progress = CrawlProgress("site", total=1)

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        progress.mark_page(_record(markdown=""))

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("no markdown" in m for m in warnings)
    assert progress.stats.empty == 1


def test_summary_counts_pages_skips_and_empties() -> None:
    progress = CrawlProgress("site", total=3)
    progress.mark_page(_record("https://example.org/a"))
    progress.mark_page(_record("https://example.org/b", markdown=""))
    progress.mark_skipped("https://example.org/c")

    assert progress.stats.summary() == "2 pages, 1 skipped, 1 without markdown"


def test_finish_reports_the_tally(caplog: pytest.LogCaptureFixture) -> None:
    progress = CrawlProgress("site", total=1)
    progress.mark_page(_record())

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        progress.finish()

    assert "1 pages, 0 skipped, 0 without markdown" in caplog.records[-1].getMessage()
