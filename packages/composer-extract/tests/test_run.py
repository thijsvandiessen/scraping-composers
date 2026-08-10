"""What a running extract reports about itself.

An extract over a large crawl runs unattended for hours; these cover the lines
that are the only evidence of what it is doing while it does it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from composer_crawler.records import CrawlRecord
from composer_extract import DocumentLedger, ExtractOptions, extract_documents
from composer_extract.schema import (
    ExtractedConcert,
    ExtractedSoloist,
    ExtractedWork,
    PageExtraction,
)

NOW = datetime(2024, 5, 1, tzinfo=UTC)
_LOGGER = "composer_extract.run"

_CONCERT = ExtractedConcert(
    date="2024-05-01",
    conductors=["Simon Rattle"],
    soloists=[ExtractedSoloist(name="Janine Jansen", discipline="violin")],
    works=[
        ExtractedWork(title="Symphony No. 5", composer="Beethoven"),
        ExtractedWork(title="Violin Concerto", composer="Brahms"),
    ],
)


class FixedExtractor:
    """Returns the same extraction for every chunk it is handed."""

    model = "test-model"

    def __init__(self, page: PageExtraction) -> None:
        self._page = page

    def extract_page(self, markdown: str, metadata: dict[str, str]) -> PageExtraction:
        return self._page

    def request_options(self) -> dict[str, object]:
        return {}


def _record(url: str) -> CrawlRecord:
    return CrawlRecord(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        fetched_at=NOW.isoformat(),
        depth=0,
        headers={},
        markdown="# Programme\nbody",
        metadata={},
    )


def _run(page: PageExtraction, *urls: str) -> None:
    list(
        extract_documents(
            [_record(url) for url in urls],
            source_name="lso",
            extractor=FixedExtractor(page),
            options=ExtractOptions(now=NOW),
        )
    )


def test_each_page_reports_what_it_produced(caplog: pytest.LogCaptureFixture) -> None:
    """Per-page document counts are how you tell "the crawl found nothing" apart
    from "the model read the pages and found nothing in them"."""
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        _run(PageExtraction(concerts=[_CONCERT]), "https://lso.co.uk/a", "https://lso.co.uk/b")

    pages = [r.getMessage() for r in caplog.records if "->" in r.getMessage()]
    # Per page: 4 people (2 composers, a conductor, a soloist) + 2 work mentions.
    assert pages == [
        "extract lso: https://lso.co.uk/a -> 6 document(s)",
        "extract lso: https://lso.co.uk/b -> 6 document(s)",
    ]


def test_a_page_the_model_found_nothing_in_is_still_counted(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        _run(PageExtraction(), "https://lso.co.uk/a")

    assert any("-> 0 document(s)" in r.getMessage() for r in caplog.records)


def test_the_run_announces_its_start_and_its_tally(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        _run(PageExtraction(concerts=[_CONCERT]), "https://lso.co.uk/a")

    messages = [r.getMessage() for r in caplog.records]
    assert any("starting (max_chars=" in m for m in messages)
    assert any("finished" in m and "1 pages" in m and "pages/min" in m for m in messages)


def test_the_reported_rate_stays_sane_on_a_fast_run(caplog: pytest.LogCaptureFixture) -> None:
    """A run short enough to divide by ~zero must not claim a six-figure rate."""
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        _run(PageExtraction(), "https://lso.co.uk/a")

    (finished,) = [r.getMessage() for r in caplog.records if "finished" in r.getMessage()]
    assert "(60.0 pages/min)" in finished


def test_a_carried_forward_page_is_logged_and_counted_separately(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A page the ledger serves is marked distinctly from one the model answered,
    both in the per-page log line and in the run's tally."""
    ledger = DocumentLedger(tmp_path / "extract-cache.db")
    page = PageExtraction(concerts=[_CONCERT])

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        docs = list(
            extract_documents(
                [_record("https://lso.co.uk/a")],
                source_name="lso",
                extractor=FixedExtractor(page),
                options=ExtractOptions(now=NOW),
                ledger=ledger,
            )
        )
        caplog.clear()
        carried = list(
            extract_documents(
                [_record("https://lso.co.uk/a")],
                source_name="lso",
                extractor=FixedExtractor(page),
                options=ExtractOptions(now=NOW),
                ledger=ledger,
            )
        )

    assert carried == docs
    messages = [r.getMessage() for r in caplog.records]
    assert any("carried forward, unchanged" in m for m in messages)
    assert any("finished" in m and "1 carried forward" in m for m in messages)
