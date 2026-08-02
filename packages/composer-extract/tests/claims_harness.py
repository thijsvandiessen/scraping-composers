"""Shared harness for the claims-extraction tests: a fake model and a crawl record."""

from __future__ import annotations

from datetime import UTC, datetime

from composer_crawler.records import CrawlRecord
from composer_extract import ExtractOptions, extract_claim_documents
from composer_extract.schema import PageClaimExtraction
from composer_schema import EntityDocument, WorkMentionDocument

NOW = datetime(2024, 5, 1, tzinfo=UTC)
URL = "https://www.laphil.com/works/violin-concerto-beethoven"


class FakeClaimExtractor:
    """Returns queued extractions (one per chunk), or the last one repeatedly."""

    def __init__(self, *pages: PageClaimExtraction) -> None:
        self._pages = list(pages)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def extract_claim_page(self, markdown: str, metadata: dict[str, str]) -> PageClaimExtraction:
        self.calls.append((markdown, metadata))
        if len(self._pages) > 1:
            return self._pages.pop(0)
        return self._pages[0]


def _record(markdown: str = "# Violin Concerto", *, url: str = URL) -> CrawlRecord:
    return CrawlRecord(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        fetched_at=NOW.isoformat(),
        depth=0,
        headers={},
        markdown=markdown,
        metadata={"title": "Violin Concerto"},
    )


def _run(*pages: PageClaimExtraction, options: ExtractOptions | None = None) -> list[object]:
    return list(
        extract_claim_documents(
            [_record()],
            source_name="laphil",
            extractor=FakeClaimExtractor(*pages),
            options=options or ExtractOptions(now=NOW),
        )
    )


def _entities(docs: list[object]) -> dict[str, EntityDocument]:
    return {d.name: d for d in docs if isinstance(d, EntityDocument)}


def _mentions(docs: list[object]) -> list[WorkMentionDocument]:
    return [d for d in docs if isinstance(d, WorkMentionDocument)]
