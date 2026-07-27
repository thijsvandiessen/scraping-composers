"""extract_documents: page extractions -> warehouse documents (no model needed)."""

from __future__ import annotations

from datetime import UTC, datetime

from composer_crawler.records import CrawlRecord
from composer_extract import ExtractOptions, extract_documents, extract_recording_documents
from composer_extract.schema import (
    ExtractedArtist,
    ExtractedConcert,
    ExtractedRecording,
    ExtractedSoloist,
    ExtractedWork,
    PageExtraction,
    PageRecordingExtraction,
)
from composer_schema import EntityDocument, WorkMentionDocument

NOW = datetime(2024, 5, 1, tzinfo=UTC)


class FakeExtractor:
    """Returns queued extractions (one per chunk), or the last one repeatedly."""

    def __init__(self, *pages: PageExtraction) -> None:
        self._pages = list(pages)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def extract_page(self, markdown: str, metadata: dict[str, str]) -> PageExtraction:
        self.calls.append((markdown, metadata))
        if len(self._pages) > 1:
            return self._pages.pop(0)
        return self._pages[0]


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


def _split(docs: list[object]) -> tuple[list[WorkMentionDocument], list[EntityDocument]]:
    mentions = [d for d in docs if isinstance(d, WorkMentionDocument)]
    entities = [d for d in docs if isinstance(d, EntityDocument)]
    return mentions, entities


_CONCERT = ExtractedConcert(
    date="2024-05-01",
    venue="Barbican",
    conductors=["Simon Rattle"],
    soloists=[ExtractedSoloist(name="Janine Jansen", discipline="violin")],
    works=[
        ExtractedWork(title="Symphony No. 5", composer="Beethoven"),
        ExtractedWork(title="Violin Concerto", composer="Brahms"),
    ],
)


def test_work_mentions_carry_concert_context() -> None:
    record = _record("# Beethoven 5\nprogramme")
    mentions, _ = _split(
        list(
            extract_documents(
                [record],
                source_name="lso",
                extractor=FakeExtractor(PageExtraction(concerts=[_CONCERT])),
                options=ExtractOptions(now=NOW),
            )
        )
    )
    assert {m.title for m in mentions} == {"Symphony No. 5", "Violin Concerto"}
    assert {m.composer for m in mentions} == {"Beethoven", "Brahms"}
    raw = mentions[0].raw
    assert raw["_source"] == "llm"
    assert raw["concert_key"] == record.final_url  # single concert -> page url
    assert raw["date"] == "2024-05-01"
    assert raw["venue"] == "Barbican"
    assert raw["conductors"] == ["Simon Rattle"]
    assert raw["soloists"] == [{"name": "Janine Jansen", "discipline": "violin"}]
    assert all(m.source_name == "lso" and m.ingested_at == NOW for m in mentions)


def test_person_entities_created_for_every_role() -> None:
    record = _record("# Beethoven 5")
    _, entities = _split(
        list(
            extract_documents(
                [record],
                source_name="lso",
                extractor=FakeExtractor(PageExtraction(concerts=[_CONCERT])),
                options=ExtractOptions(now=NOW),
            )
        )
    )
    by_name = {e.name: e for e in entities}
    assert set(by_name) == {"Beethoven", "Brahms", "Simon Rattle", "Janine Jansen"}
    assert all(e.kind == "person" for e in entities)

    def professions(name: str) -> set[str | None]:
        return {c.object_label for c in by_name[name].claims if c.predicate == "has_profession"}

    assert professions("Simon Rattle") == {"conductor"}
    assert professions("Janine Jansen") == {"soloist"}
    assert professions("Beethoven") == {"composer"}


def test_person_in_multiple_roles_gets_one_entity_with_both_professions() -> None:
    concert = ExtractedConcert(
        conductors=["Pierre Boulez"],
        works=[ExtractedWork(title="Notations", composer="Pierre Boulez")],
    )
    record = _record("# Boulez")
    _, entities = _split(
        list(
            extract_documents(
                [record],
                source_name="lso",
                extractor=FakeExtractor(PageExtraction(concerts=[concert])),
                options=ExtractOptions(now=NOW),
            )
        )
    )
    boulez = [e for e in entities if e.name == "Pierre Boulez"]
    assert len(boulez) == 1
    assert {c.object_label for c in boulez[0].claims} == {"composer", "conductor"}


def test_multiple_concerts_get_distinct_concert_keys() -> None:
    page = PageExtraction(
        concerts=[
            ExtractedConcert(date="2024-05-01", works=[ExtractedWork(title="A")]),
            ExtractedConcert(date="2024-05-02", works=[ExtractedWork(title="B")]),
        ]
    )
    record = _record("# season")
    mentions, _ = _split(
        list(
            extract_documents(
                [record], source_name="lso", extractor=FakeExtractor(page), options=ExtractOptions(now=NOW)
            )
        )
    )
    keys = {m.raw["concert_key"] for m in mentions}
    assert keys == {f"{record.final_url}#2024-05-01", f"{record.final_url}#2024-05-02"}


def test_page_without_concerts_yields_nothing() -> None:
    record = _record("# about us")
    docs = list(
        extract_documents(
            [record],
            source_name="lso",
            extractor=FakeExtractor(PageExtraction()),
            options=ExtractOptions(now=NOW),
        )
    )
    assert docs == []


class FakeRecordingExtractor:
    """Returns queued recording extractions (one per chunk), or the last repeatedly."""

    def __init__(self, *pages: PageRecordingExtraction) -> None:
        self._pages = list(pages)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def extract_recording_page(self, markdown: str, metadata: dict[str, str]) -> PageRecordingExtraction:
        self.calls.append((markdown, metadata))
        if len(self._pages) > 1:
            return self._pages.pop(0)
        return self._pages[0]


_RECORDING = ExtractedRecording(
    title="Beethoven: Symphony No. 9",
    release_date="2024-03-15",
    label="Deutsche Grammophon",
    catalogue_number="486 1234",
    format="CD",
    artists=[
        ExtractedArtist(name="Simon Rattle", role="conductor"),
        ExtractedArtist(name="Janine Jansen", role="soloist", discipline="violin"),
    ],
    works=[ExtractedWork(title="Symphony No. 9", composer="Beethoven")],
)


def _recording_docs(*recordings: ExtractedRecording) -> list[object]:
    record = _record("# album", url="https://www.deutschegrammophon.com/en/album")
    return list(
        extract_recording_documents(
            [record],
            source_name="deutschegrammophon",
            extractor=FakeRecordingExtractor(PageRecordingExtraction(recordings=list(recordings))),
            options=ExtractOptions(now=NOW),
        )
    )


def test_recording_work_mentions_carry_release_context() -> None:
    mentions, _ = _split(_recording_docs(_RECORDING))
    assert {m.title for m in mentions} == {"Symphony No. 9"}
    raw = mentions[0].raw
    assert raw["_source"] == "llm"
    assert raw["_kind"] == "recording"
    assert raw["record_key"] == "https://www.deutschegrammophon.com/en/album#486 1234"
    assert raw["title"] == "Beethoven: Symphony No. 9"
    assert raw["release_date"] == "2024-03-15"
    assert raw["label"] == "Deutsche Grammophon"
    assert raw["catalogue_number"] == "486 1234"
    assert raw["format"] == "CD"
    assert raw["artists"] == [
        {"name": "Simon Rattle", "role": "conductor", "discipline": None},
        {"name": "Janine Jansen", "role": "soloist", "discipline": "violin"},
    ]


def test_recording_entities_created_for_artists_and_composers() -> None:
    _, entities = _split(_recording_docs(_RECORDING))
    by_name = {e.name: e for e in entities}
    assert set(by_name) == {"Beethoven", "Simon Rattle", "Janine Jansen"}
    assert all(e.kind == "person" for e in entities)

    def professions(name: str) -> set[str | None]:
        return {c.object_label for c in by_name[name].claims if c.predicate == "has_profession"}

    assert professions("Simon Rattle") == {"conductor"}
    assert professions("Janine Jansen") == {"soloist"}
    assert professions("Beethoven") == {"composer"}


def test_recording_key_falls_back_to_url_without_catalogue() -> None:
    recording = ExtractedRecording(title="Untitled", works=[ExtractedWork(title="A")])
    mentions, _ = _split(_recording_docs(recording))
    assert mentions[0].raw["record_key"] == "https://www.deutschegrammophon.com/en/album"


def test_concerts_merge_across_markdown_chunks() -> None:
    first = PageExtraction(concerts=[ExtractedConcert(date="2024-01-01", works=[ExtractedWork(title="A")])])
    second = PageExtraction(concerts=[ExtractedConcert(date="2024-02-02", works=[ExtractedWork(title="B")])])
    # Two heading sections, each under the tiny cap -> two chunks -> two calls.
    markdown = "## One\n" + "x" * 30 + "\n## Two\n" + "y" * 30
    record = _record(markdown)
    extractor = FakeExtractor(first, second)
    mentions, _ = _split(
        list(
            extract_documents(
                [record],
                source_name="lso",
                extractor=extractor,
                options=ExtractOptions(max_chars=50, now=NOW),
            )
        )
    )
    assert {m.title for m in mentions} == {"A", "B"}
    assert len(extractor.calls) == 2
