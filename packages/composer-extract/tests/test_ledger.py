"""Skipping a whole page's extraction, not just its model answer.

These pin the ledger's own contract, one level below the answer cache: a page
whose content and extractor fingerprint match what is on record for a kind is
served straight back, and every input that could make that answer wrong — the
content, the model, the prompt, the schema, the options, the kind itself — misses.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from composer_extract import DocumentLedger, LedgerKey, open_ledger, request_fingerprint
from composer_extract.schema import PageExtraction, PageRecordingExtraction
from composer_schema.testing import mention, person

_DOCS = [person("Beethoven, Ludwig van"), mention("Symphony No. 5", "Beethoven, Ludwig van")]


class FakeExtractor:
    def __init__(self, model: str = "qwen2.5") -> None:
        self.model = model

    def request_options(self) -> dict[str, Any]:
        return {"temperature": 0}


@pytest.fixture(name="ledger")
def ledger_fixture(tmp_path: Path) -> DocumentLedger:
    return DocumentLedger(tmp_path / "extract-cache.db")


def _fingerprint(model: str = "qwen2.5") -> str:
    return request_fingerprint(FakeExtractor(model), "system prompt", PageExtraction)


def _key(
    *,
    source: str = "lso",
    final_url: str = "https://lso.co.uk/a",
    kind: str = "concerts",
    content_sha256: str = "h1",
    extractor_fingerprint: str | None = None,
) -> LedgerKey:
    return LedgerKey(
        source=source,
        final_url=final_url,
        kind=kind,
        content_sha256=content_sha256,
        extractor_fingerprint=extractor_fingerprint if extractor_fingerprint is not None else _fingerprint(),
    )


def test_an_unchanged_page_is_served_from_the_ledger(ledger: DocumentLedger) -> None:
    key = _key()
    ledger.put(key, _DOCS)

    assert ledger.get(key) == _DOCS


def test_a_page_never_recorded_misses(ledger: DocumentLedger) -> None:
    assert ledger.get(_key()) is None


def test_changed_content_misses(ledger: DocumentLedger) -> None:
    fp = _fingerprint()
    ledger.put(_key(content_sha256="h1", extractor_fingerprint=fp), _DOCS)

    assert ledger.get(_key(content_sha256="h2", extractor_fingerprint=fp)) is None


def test_changed_extractor_fingerprint_misses(ledger: DocumentLedger) -> None:
    """The regression guard: a model/prompt/schema/options change must not serve
    a stale answer just because the page's own text is unchanged."""
    ledger.put(_key(extractor_fingerprint=_fingerprint("qwen2.5")), _DOCS)

    assert ledger.get(_key(extractor_fingerprint=_fingerprint("llama3.1"))) is None


def test_a_different_kind_does_not_share_the_entry(ledger: DocumentLedger) -> None:
    fp = _fingerprint()
    ledger.put(_key(kind="concerts", extractor_fingerprint=fp), _DOCS)

    assert ledger.get(_key(kind="claims", extractor_fingerprint=fp)) is None


def test_a_different_source_does_not_share_the_entry(ledger: DocumentLedger) -> None:
    """Two crawl configs can share a domain; their ledgers must not cross-hit."""
    fp = _fingerprint()
    ledger.put(_key(source="lso", final_url="https://example.org/a", extractor_fingerprint=fp), _DOCS)

    assert ledger.get(_key(source="rco", final_url="https://example.org/a", extractor_fingerprint=fp)) is None


def test_request_fingerprint_changes_with_model_prompt_schema_or_options() -> None:
    base = request_fingerprint(FakeExtractor("qwen2.5"), "prompt A", PageExtraction)
    assert request_fingerprint(FakeExtractor("llama3.1"), "prompt A", PageExtraction) != base
    assert request_fingerprint(FakeExtractor("qwen2.5"), "prompt B", PageExtraction) != base
    assert request_fingerprint(FakeExtractor("qwen2.5"), "prompt A", PageRecordingExtraction) != base

    class TunedExtractor(FakeExtractor):
        def request_options(self) -> dict[str, Any]:
            return {"temperature": 0, "num_ctx": 8192}

    assert request_fingerprint(TunedExtractor("qwen2.5"), "prompt A", PageExtraction) != base


def test_a_damaged_row_is_dropped_rather_than_raised(ledger: DocumentLedger) -> None:
    """One corrupt entry should cost a single re-extraction, not fail the page."""
    key = _key()
    ledger.put(key, _DOCS)
    with sqlite3.connect(ledger.path) as connection:
        connection.execute("UPDATE document_extract_state SET documents = 'not json'")

    assert ledger.get(key) is None


def test_a_row_whose_documents_no_longer_deserialize_is_dropped_rather_than_raised(
    ledger: DocumentLedger,
) -> None:
    """Valid JSON, but a shape :func:`composer_schema.deserialize_document` no
    longer recognises — the dataclass moved on since the row was written."""
    key = _key()
    ledger.put(key, _DOCS)
    with sqlite3.connect(ledger.path) as connection:
        connection.execute(
            "UPDATE document_extract_state SET documents = ?", (json.dumps([{"_type": "bogus"}]),)
        )

    assert ledger.get(key) is None


def test_put_replaces_the_prior_entry_for_the_same_key(ledger: DocumentLedger) -> None:
    fp = _fingerprint()
    ledger.put(_key(content_sha256="h1", extractor_fingerprint=fp), _DOCS)
    ledger.put(_key(content_sha256="h2", extractor_fingerprint=fp), [])

    assert ledger.get(_key(content_sha256="h2", extractor_fingerprint=fp)) == []
    assert ledger.get(_key(content_sha256="h1", extractor_fingerprint=fp)) is None


def test_a_missing_ledger_file_is_created_on_first_use(tmp_path: Path) -> None:
    ledger = DocumentLedger(tmp_path / "nested" / "extract-cache.db")
    ledger.put(_key(), [])

    assert ledger.path.exists()


def test_the_summary_reports_what_was_saved(ledger: DocumentLedger) -> None:
    fp = _fingerprint()
    ledger.put(_key(final_url="https://lso.co.uk/a", extractor_fingerprint=fp), _DOCS)
    ledger.get(_key(final_url="https://lso.co.uk/a", extractor_fingerprint=fp))
    ledger.get(_key(final_url="https://lso.co.uk/b", extractor_fingerprint=fp))

    assert ledger.hits == 1
    assert ledger.misses == 1
    assert "50% of pages skipped" in ledger.summary()


def test_open_ledger_returns_nothing_when_switched_off(tmp_path: Path) -> None:
    assert open_ledger(tmp_path / "l.db", enabled=False) is None
    assert open_ledger(tmp_path / "l.db", enabled=True) is not None
