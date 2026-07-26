"""record_markdown / chunk_markdown behaviour (no model, no network)."""

from __future__ import annotations

from composer_crawler.records import CrawlRecord
from composer_extract.markdown import chunk_markdown, record_markdown


def _record(*, markdown: str) -> CrawlRecord:
    return CrawlRecord(
        url="https://example.org/x",
        final_url="https://example.org/x",
        status_code=200,
        content_type="text/html",
        fetched_at="2024-01-01T00:00:00+00:00",
        depth=0,
        headers={},
        markdown=markdown,
    )


def test_record_markdown_is_the_stored_markdown() -> None:
    assert record_markdown(_record(markdown="  # Stored\ncontent  ")) == "# Stored\ncontent"


def test_record_markdown_empty_when_page_had_none() -> None:
    assert record_markdown(_record(markdown="")) == ""


def test_chunk_markdown_short_text_is_single_chunk() -> None:
    assert chunk_markdown("small page", 100) == ["small page"]


def test_chunk_markdown_empty_yields_no_chunks() -> None:
    assert chunk_markdown("   ", 100) == []


def test_chunk_markdown_splits_on_headings_within_cap() -> None:
    markdown = "## One\n" + "x" * 30 + "\n## Two\n" + "y" * 30
    chunks = chunk_markdown(markdown, 50)
    assert len(chunks) == 2
    assert all(len(chunk) <= 50 for chunk in chunks)
    assert chunks[0].startswith("## One")
    assert chunks[1].startswith("## Two")


def test_chunk_markdown_hard_splits_oversized_section() -> None:
    markdown = "## Big\n" + "z" * 250
    chunks = chunk_markdown(markdown, 100)
    assert len(chunks) >= 3
    assert all(len(chunk) <= 100 for chunk in chunks)
