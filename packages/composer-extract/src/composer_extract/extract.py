"""Turn crawled pages into the warehouse's document types via the LLM.

Two extraction modes share one shape. In *concerts* mode each performed work
becomes a :class:`WorkMentionDocument` carrying the concert's performance
context in ``raw`` (marked ``_source: "llm"``); in *recordings* mode each work
on an album becomes a mention carrying the release context in ``raw`` (marked
``_source: "llm"``, ``_kind: "recording"``). Either way every distinct person
(composer, conductor, soloist, ...) becomes an :class:`EntityDocument`, and the
verbatim name is reused so the matching derive pass resolves participants to
their entities.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import Protocol

from composer_crawler.records import CrawlRecord
from composer_schema import EntityDocument, SourceClaim, WorkMentionDocument

from .markdown import chunk_markdown, record_markdown
from .resilience import extract_chunks
from .run import ExtractOptions, ExtractRun
from .schema import ExtractedConcert, ExtractedRecording, PageExtraction, PageRecordingExtraction

_LLM_SOURCE_MARKER = "llm"
_RECORDING_KIND = "recording"


class PageExtractor(Protocol):
    """Anything that turns a markdown chunk into a :class:`PageExtraction`."""

    def extract_page(self, markdown: str, metadata: dict[str, str]) -> PageExtraction: ...


class RecordingPageExtractor(Protocol):
    """Anything that turns a markdown chunk into a :class:`PageRecordingExtraction`."""

    def extract_recording_page(self, markdown: str, metadata: dict[str, str]) -> PageRecordingExtraction: ...


def _person_docs(
    roles: dict[str, set[str]], url: str, source_name: str, now: datetime
) -> Iterator[EntityDocument]:
    """One entity per named person, tagged with the profession(s) they appear in."""
    for name, professions in roles.items():
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


# --- concerts ---------------------------------------------------------------


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


def _concert_roles(concerts: Iterable[ExtractedConcert]) -> dict[str, set[str]]:
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


def _page_concerts(record: CrawlRecord, extractor: PageExtractor, run: ExtractRun) -> list[ExtractedConcert]:
    chunks = chunk_markdown(record_markdown(record), run.max_chars)
    pages = extract_chunks(
        chunks, extractor.extract_page, record.metadata, url=record.final_url, stats=run.stats
    )
    return [concert for page in pages for concert in page.concerts]


def _emit_concerts(
    record: CrawlRecord, extractor: PageExtractor, run: ExtractRun
) -> Iterator[EntityDocument | WorkMentionDocument]:
    concerts = _page_concerts(record, extractor, run)
    if not concerts:
        return
    url = record.final_url
    yield from _person_docs(_concert_roles(concerts), url, run.source_name, run.now)
    for index, concert in enumerate(concerts):
        key = _concert_key(url, concert, index, len(concerts))
        yield from _work_mentions(concert, key, url, run.source_name, run.now)


# --- recordings -------------------------------------------------------------


def _recording_key(final_url: str, recording: ExtractedRecording, index: int, total: int) -> str:
    """A stable per-recording identity: the catalogue number when present (it is
    the label's own release id), else the page url disambiguated by position."""
    if recording.catalogue_number:
        return f"{final_url}#{recording.catalogue_number.strip()}"
    if total == 1:
        return final_url
    return f"{final_url}#r{index}"


def _recording_raw(record_key: str, url: str, recording: ExtractedRecording) -> dict[str, object]:
    return {
        "_source": _LLM_SOURCE_MARKER,
        "_kind": _RECORDING_KIND,
        "record_key": record_key,
        "url": url,
        "title": recording.title,
        "release_date": recording.release_date,
        "label": recording.label,
        "catalogue_number": recording.catalogue_number,
        "format": recording.format,
        "artists": [{"name": a.name, "role": a.role, "discipline": a.discipline} for a in recording.artists],
    }


def _recording_work_mentions(
    recording: ExtractedRecording, record_key: str, url: str, source_name: str, now: datetime
) -> Iterator[WorkMentionDocument]:
    raw = _recording_raw(record_key, url, recording)
    for i, work in enumerate(recording.works):
        title = work.title.strip()
        if not title:
            continue
        yield WorkMentionDocument(
            id=f"{record_key}#w{i}",
            url=url,
            source_name=source_name,
            ingested_at=now,
            title=title,
            composer=work.composer,
            raw=raw,
        )


def _artist_profession(role: str | None) -> str:
    """Map an artist's recording role to a profession label for their entity."""
    normalized = (role or "").strip().lower()
    if normalized in {"conductor", "soloist", "ensemble"}:
        return normalized
    return "performer"


def _recording_roles(recordings: Iterable[ExtractedRecording]) -> dict[str, set[str]]:
    roles: dict[str, set[str]] = {}
    for recording in recordings:
        for work in recording.works:
            if work.composer:
                roles.setdefault(work.composer.strip(), set()).add("composer")
        for artist in recording.artists:
            roles.setdefault(artist.name.strip(), set()).add(_artist_profession(artist.role))
    roles.pop("", None)
    return roles


def _page_recordings(
    record: CrawlRecord, extractor: RecordingPageExtractor, run: ExtractRun
) -> list[ExtractedRecording]:
    chunks = chunk_markdown(record_markdown(record), run.max_chars)
    pages = extract_chunks(
        chunks, extractor.extract_recording_page, record.metadata, url=record.final_url, stats=run.stats
    )
    return [recording for page in pages for recording in page.recordings]


def _emit_recordings(
    record: CrawlRecord, extractor: RecordingPageExtractor, run: ExtractRun
) -> Iterator[EntityDocument | WorkMentionDocument]:
    recordings = _page_recordings(record, extractor, run)
    if not recordings:
        return
    url = record.final_url
    yield from _person_docs(_recording_roles(recordings), url, run.source_name, run.now)
    for index, recording in enumerate(recordings):
        key = _recording_key(url, recording, index, len(recordings))
        yield from _recording_work_mentions(recording, key, url, run.source_name, run.now)


def extract_documents(
    records: Iterable[CrawlRecord],
    *,
    source_name: str,
    extractor: PageExtractor,
    options: ExtractOptions | None = None,
) -> Iterator[EntityDocument | WorkMentionDocument]:
    """Yield entity/work-mention documents from crawled *records* (concert mode)."""
    run = ExtractRun.start(source_name, options)
    for record in records:
        yield from _emit_concerts(record, extractor, run)
        run.mark_page()
    run.finish()


def extract_recording_documents(
    records: Iterable[CrawlRecord],
    *,
    source_name: str,
    extractor: RecordingPageExtractor,
    options: ExtractOptions | None = None,
) -> Iterator[EntityDocument | WorkMentionDocument]:
    """Yield entity/work-mention documents from crawled *records* (recording mode)."""
    run = ExtractRun.start(source_name, options)
    for record in records:
        yield from _emit_recordings(record, extractor, run)
        run.mark_page()
    run.finish()
