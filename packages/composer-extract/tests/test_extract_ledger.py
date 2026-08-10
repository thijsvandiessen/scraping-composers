"""extract_documents/extract_recording_documents wired to a DocumentLedger.

Split out from test_extract.py (which covers page-extraction shape, no model
needed) since these need their own DocumentLedger-aware fakes and cover a
different concern: whether a second run skips the model call entirely for an
unchanged page, not what one page's extraction looks like.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from composer_crawler.records import CrawlRecord
from composer_extract import (
    DocumentLedger,
    ExtractOptions,
    extract_documents,
    extract_recording_documents,
)
from composer_extract.schema import (
    ExtractedArtist,
    ExtractedConcert,
    ExtractedRecording,
    ExtractedSoloist,
    ExtractedWork,
    PageExtraction,
    PageRecordingExtraction,
)

NOW = datetime(2024, 5, 1, tzinfo=UTC)


class FakeExtractor:
    """Returns the same extraction for every chunk; a ledger fingerprint source."""

    def __init__(self, page: PageExtraction, model: str = "test-model") -> None:
        self._page = page
        self.model = model
        self.calls: list[tuple[str, dict[str, str]]] = []

    def extract_page(self, markdown: str, metadata: dict[str, str]) -> PageExtraction:
        self.calls.append((markdown, metadata))
        return self._page

    def request_options(self) -> dict[str, object]:
        return {}


class FakeRecordingExtractor:
    """Returns the same recording extraction for every chunk."""

    def __init__(self, page: PageRecordingExtraction, model: str = "test-model") -> None:
        self._page = page
        self.model = model
        self.calls: list[tuple[str, dict[str, str]]] = []

    def extract_recording_page(self, markdown: str, metadata: dict[str, str]) -> PageRecordingExtraction:
        self.calls.append((markdown, metadata))
        return self._page

    def request_options(self) -> dict[str, object]:
        return {}


def _record(markdown: str, *, url: str = "https://lso.co.uk/whats-on/beethoven-5") -> CrawlRecord:
    return CrawlRecord(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        fetched_at=NOW.isoformat(),
        depth=0,
        headers={},
        markdown=markdown,
        metadata={"title": "Beethoven 5"},
    )


_CONCERT = ExtractedConcert(
    date="2024-05-01",
    venue="Barbican",
    conductors=["Simon Rattle"],
    soloists=[ExtractedSoloist(name="Janine Jansen", discipline="violin")],
    works=[ExtractedWork(title="Symphony No. 5", composer="Beethoven")],
)

_RECORDING = ExtractedRecording(
    title="Beethoven: Symphony No. 9",
    label="Deutsche Grammophon",
    artists=[ExtractedArtist(name="Simon Rattle", role="conductor")],
    works=[ExtractedWork(title="Symphony No. 9", composer="Beethoven")],
)


def test_a_ledgered_page_is_never_sent_to_the_extractor_again(tmp_path: Path) -> None:
    """The whole point: a second extract of an unchanged page calls the model
    zero times, not once-but-cached — chunking and prompting never happen."""
    ledger = DocumentLedger(tmp_path / "extract-cache.db")
    record = _record("# Beethoven 5\nprogramme")
    extractor = FakeExtractor(PageExtraction(concerts=[_CONCERT]))
    options = ExtractOptions(now=NOW)

    first = list(
        extract_documents([record], source_name="lso", extractor=extractor, options=options, ledger=ledger)
    )
    second = list(
        extract_documents([record], source_name="lso", extractor=extractor, options=options, ledger=ledger)
    )

    assert len(extractor.calls) == 1
    assert second == first


def test_changed_content_is_re_extracted(tmp_path: Path) -> None:
    ledger = DocumentLedger(tmp_path / "extract-cache.db")
    extractor = FakeExtractor(PageExtraction(concerts=[_CONCERT]))
    options = ExtractOptions(now=NOW)

    list(
        extract_documents(
            [_record("# Beethoven 5\nprogramme")],
            source_name="lso",
            extractor=extractor,
            options=options,
            ledger=ledger,
        )
    )
    list(
        extract_documents(
            [_record("# Beethoven 5\nprogramme (rescheduled)")],
            source_name="lso",
            extractor=extractor,
            options=options,
            ledger=ledger,
        )
    )

    assert len(extractor.calls) == 2


def test_a_changed_extractor_fingerprint_re_extracts_even_unchanged_content(tmp_path: Path) -> None:
    """The regression guard for the ledger's whole reason to key on more than
    content: a model/prompt/options change must not be served a stale answer."""
    ledger = DocumentLedger(tmp_path / "extract-cache.db")
    record = _record("# Beethoven 5\nprogramme")
    options = ExtractOptions(now=NOW)

    list(
        extract_documents(
            [record],
            source_name="lso",
            extractor=FakeExtractor(PageExtraction(concerts=[_CONCERT])),
            options=options,
            ledger=ledger,
        )
    )
    other_model = FakeExtractor(PageExtraction(concerts=[_CONCERT]), model="a-different-model")
    list(
        extract_documents([record], source_name="lso", extractor=other_model, options=options, ledger=ledger)
    )

    assert len(other_model.calls) == 1


def test_concerts_and_recordings_do_not_share_a_ledger_entry(tmp_path: Path) -> None:
    """Same page, same source, two kinds enabled: a hit for one must not be
    served for the other."""
    ledger = DocumentLedger(tmp_path / "extract-cache.db")
    record = _record("# Album\nnotes")
    options = ExtractOptions(now=NOW)

    list(
        extract_documents(
            [record],
            source_name="dg",
            extractor=FakeExtractor(PageExtraction(concerts=[_CONCERT])),
            options=options,
            ledger=ledger,
        )
    )
    recording_extractor = FakeRecordingExtractor(PageRecordingExtraction(recordings=[_RECORDING]))
    list(
        extract_recording_documents(
            [record], source_name="dg", extractor=recording_extractor, options=options, ledger=ledger
        )
    )

    assert len(recording_extractor.calls) == 1
