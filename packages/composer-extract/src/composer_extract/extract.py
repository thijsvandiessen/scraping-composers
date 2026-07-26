"""Turn crawled pages into the warehouse's document types via the LLM.

Each page's markdown is extracted into concerts; every performed work becomes a
:class:`WorkMentionDocument` carrying the concert's performance context in
``raw`` (the shape ``derive_concerts`` reads, marked ``_source: "llm"``), and
every distinct person (composer, conductor, soloist) becomes an
:class:`EntityDocument` so previously-unseen performers enter the warehouse. The
verbatim name is reused across both so concert derivation resolves participants
to their entities.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import Protocol

from composer_config import settings
from composer_crawler.records import CrawlRecord
from composer_schema import EntityDocument, SourceClaim, WorkMentionDocument

from .markdown import chunk_markdown, record_markdown
from .schema import ExtractedConcert, PageExtraction

_LLM_SOURCE_MARKER = "llm"


class PageExtractor(Protocol):
    """Anything that turns a markdown chunk into a :class:`PageExtraction`."""

    def extract_page(self, markdown: str, metadata: dict[str, str]) -> PageExtraction: ...


def _concert_key(final_url: str, concert: ExtractedConcert, index: int, total: int) -> str:
    """A stable per-concert identity; a page with several concerts disambiguates
    by date (or position) so ``derive_concerts`` keeps them apart."""
    if total == 1:
        return final_url
    return f"{final_url}#{concert.date or f'c{index}'}"


def _concert_raw(concert_key: str, url: str, concert: ExtractedConcert) -> dict[str, object]:
    return {
        "_source": _LLM_SOURCE_MARKER,
        "concert_key": concert_key,
        "url": url,
        "date": concert.date,
        "venue": concert.venue,
        "conductors": list(concert.conductors),
        "soloists": [{"name": s.name, "discipline": s.discipline} for s in concert.soloists],
    }


def _work_mentions(
    concert: ExtractedConcert, concert_key: str, url: str, source_name: str, now: datetime
) -> Iterator[WorkMentionDocument]:
    raw = _concert_raw(concert_key, url, concert)
    for i, work in enumerate(concert.works):
        title = work.title.strip()
        if not title:
            continue
        yield WorkMentionDocument(
            id=f"{concert_key}#w{i}",
            url=url,
            source_name=source_name,
            ingested_at=now,
            title=title,
            composer=work.composer,
            raw=raw,
        )


def _roles_by_person(concerts: Iterable[ExtractedConcert]) -> dict[str, set[str]]:
    """Every named person on the page mapped to the profession(s) they appear in."""
    roles: dict[str, set[str]] = {}
    for concert in concerts:
        for work in concert.works:
            if work.composer:
                roles.setdefault(work.composer.strip(), set()).add("composer")
        for name in concert.conductors:
            roles.setdefault(name.strip(), set()).add("conductor")
        for soloist in concert.soloists:
            roles.setdefault(soloist.name.strip(), set()).add("soloist")
    roles.pop("", None)
    return roles


def _person_docs(
    concerts: list[ExtractedConcert], url: str, source_name: str, now: datetime
) -> Iterator[EntityDocument]:
    for name, professions in _roles_by_person(concerts).items():
        claims = tuple(
            SourceClaim(predicate="has_profession", object_kind="profession", object_label=profession)
            for profession in sorted(professions)
        )
        yield EntityDocument(
            id=f"person:{name}",
            url=url,
            source_name=source_name,
            ingested_at=now,
            name=name,
            kind="person",
            claims=claims,
        )


def _page_concerts(record: CrawlRecord, extractor: PageExtractor, max_chars: int) -> list[ExtractedConcert]:
    markdown = record_markdown(record)
    concerts: list[ExtractedConcert] = []
    for chunk in chunk_markdown(markdown, max_chars):
        concerts.extend(extractor.extract_page(chunk, record.metadata).concerts)
    return concerts


def extract_documents(
    records: Iterable[CrawlRecord],
    *,
    source_name: str,
    extractor: PageExtractor,
    max_chars: int | None = None,
    now: datetime | None = None,
) -> Iterator[EntityDocument | WorkMentionDocument]:
    """Yield entity/work-mention documents extracted from crawled *records*."""
    max_chars = max_chars if max_chars is not None else settings.extract_max_chars
    stamp = now or datetime.now(UTC)
    for record in records:
        concerts = _page_concerts(record, extractor, max_chars)
        if not concerts:
            continue
        url = record.final_url
        yield from _person_docs(concerts, url, source_name, stamp)
        for index, concert in enumerate(concerts):
            key = _concert_key(url, concert, index, len(concerts))
            yield from _work_mentions(concert, key, url, source_name, stamp)
