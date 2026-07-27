"""One unusable model response must not kill a run — but a dead model still must."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from composer_crawler.records import CrawlRecord
from composer_extract import ExtractOptions, extract_documents
from composer_extract.resilience import ExtractAborted, ExtractStats, extract_chunks
from composer_extract.schema import ExtractedConcert, ExtractedWork, PageExtraction

NOW = datetime(2024, 5, 1, tzinfo=UTC)
URL = "https://lso.co.uk/whats-on/beethoven-5"

CONCERT = ExtractedConcert(date="2024-05-01", works=[ExtractedWork(title="Symphony No. 5")])


def _record(markdown: str, *, url: str = URL) -> CrawlRecord:
    return CrawlRecord(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        fetched_at=NOW.isoformat(),
        depth=0,
        headers={},
        markdown=markdown,
        metadata={},
    )


def _invalid_json() -> Exception:
    """The failure the real model produces: a truncated answer that will not validate."""
    with pytest.raises(ValueError) as excinfo:
        PageExtraction.model_validate_json('{"concerts": [{"date": "2024-05-01')
    return excinfo.value


class FailingExtractor:
    """Fails on any chunk longer than *fails_over*; succeeds on the rest."""

    def __init__(self, fails_over: int = 0) -> None:
        self._fails_over = fails_over
        self.seen: list[str] = []

    def extract_page(self, markdown: str, metadata: dict[str, str]) -> PageExtraction:
        self.seen.append(markdown)
        if len(markdown) > self._fails_over:
            raise _invalid_json()
        return PageExtraction(concerts=[CONCERT])


def _chunks(chunks: list[str], extractor: FailingExtractor, stats: ExtractStats) -> list[PageExtraction]:
    return list(extract_chunks(chunks, extractor.extract_page, {}, url=URL, stats=stats))


def test_retries_a_failed_chunk_on_its_halves() -> None:
    chunk = "# One\n" + "a" * 40 + "\n# Two\n" + "b" * 40
    extractor = FailingExtractor(fails_over=len(chunk) - 1)
    stats = ExtractStats()

    pages = _chunks([chunk], extractor, stats)

    assert [len(p.concerts) for p in pages] == [1, 1], "both halves should have been re-asked"
    assert (stats.chunks, stats.retried, stats.failed) == (1, 1, 0)


def test_skips_a_chunk_that_fails_twice_without_killing_the_run() -> None:
    """The bug: an unusable response used to abort the whole snapshot."""
    good, bad = "# Good\nshort", "# Bad\n" + "x" * 200
    extractor = FailingExtractor(fails_over=len("# Good\nshort"))
    stats = ExtractStats()

    pages = _chunks([good, bad], extractor, stats)

    assert len(pages) == 1, "the good chunk still comes through"
    assert stats.failed == 2, "both halves of the bad chunk were given up on"
    assert stats.retried == 1


def test_unsplittable_chunk_is_skipped_without_a_retry() -> None:
    extractor = FailingExtractor(fails_over=0)
    stats = ExtractStats()

    assert _chunks(["x"], extractor, stats) == []
    assert extractor.seen == ["x"], "nothing to split, so no second call"
    assert (stats.retried, stats.failed) == (0, 1)


def test_transport_failures_still_abort_the_run() -> None:
    """Ollama being down is not a bad page: a run that quietly extracts nothing
    from 10 000 pages is worse than one that fails."""

    class DeadOllama:
        def extract_page(self, markdown: str, metadata: dict[str, str]) -> PageExtraction:
            raise ConnectionError("ollama is not running")

    with pytest.raises(ConnectionError):
        list(extract_chunks(["# A\nbody"], DeadOllama().extract_page, {}, url=URL, stats=ExtractStats()))


def test_gives_up_after_too_many_consecutive_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    from composer_config import settings

    monkeypatch.setattr(settings, "extract_max_consecutive_failures", 2)
    extractor = FailingExtractor(fails_over=0)

    with pytest.raises(ExtractAborted):
        _chunks(["a", "b", "c"], extractor, ExtractStats())


def test_a_bad_page_does_not_stop_the_pages_after_it() -> None:
    extractor = FailingExtractor(fails_over=len("# Fine\nshort"))
    options = ExtractOptions(now=NOW)

    docs = list(
        extract_documents(
            [_record("# Bad\n" + "x" * 400, url="https://lso.co.uk/a"), _record("# Fine\nshort")],
            source_name="lso",
            extractor=extractor,
            options=options,
        )
    )

    assert any(getattr(doc, "title", None) == "Symphony No. 5" for doc in docs)
    assert options.stats.pages == 2
    assert options.stats.failed > 0


def test_error_text_in_the_log_is_bounded(caplog: pytest.LogCaptureFixture) -> None:
    """A validation error quotes the model's whole output — the 57k-line answer
    that started this must not be copied into the log."""
    with caplog.at_level("WARNING"):
        _chunks(["x"], FailingExtractor(fails_over=0), ExtractStats())

    assert caplog.records, "a skipped chunk must be reported"
    message = caplog.records[0].getMessage()
    assert URL in message
    assert len(message) < 500


def test_every_chunk_is_timed_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    """One page can be many model calls; per-chunk timing is what shows where the
    hours in a long extract are actually going."""
    with caplog.at_level("DEBUG", logger="composer_extract.resilience"):
        _chunks(["# A\nbody", "# B\nbody"], FailingExtractor(fails_over=1000), ExtractStats())

    messages = [r.getMessage() for r in caplog.records]
    assert any("chunk 1" in m and "chars" in m for m in messages)
    assert any("chunk 2 yielded 1 extraction(s)" in m for m in messages)


def test_a_degrading_run_warns_before_it_gives_up(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The abort is the last word, not the first warning: a streak halfway to the
    limit is the point at which it is still worth going to look at the model."""
    from composer_config import settings

    monkeypatch.setattr(settings, "extract_max_consecutive_failures", 4)

    with caplog.at_level("WARNING", logger="composer_extract.resilience"):
        _chunks(["a", "b"], FailingExtractor(fails_over=0), ExtractStats())

    warnings = [r.getMessage() for r in caplog.records if "in a row" in r.getMessage()]
    assert warnings == [f"extract {URL}: 2 chunk(s) in a row unusable; the run aborts at 4"]
