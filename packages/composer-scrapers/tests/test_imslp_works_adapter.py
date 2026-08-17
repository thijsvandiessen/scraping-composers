"""Tests for the ImslpWorksAdapter's document output.

The pairing matters: the warehouse routes WorkMentionDocument and
EntityDocument down different paths (canonical works vs entities + claims),
so an IMSLP work has to arrive as both to be fully represented — the same
reasoning ``test_boosey_adapter.py`` documents for its own source.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from composer_schema import EntityDocument, WorkMentionDocument
from composer_scrapers import REGISTRY
from composer_scrapers.imslp_works import ImslpWorksAdapter
from composer_scrapers.imslp_works.gold import GoldComposer
from test_imslp_works import WORK_PAGE, WORK_PAGE_SPARSE

PageTuple = tuple[GoldComposer, str, str, str]

BEETHOVEN = GoldComposer(entity_id="c1", label="Beethoven, Ludwig van", known_imslp_url=None)

SONATA = (
    BEETHOVEN,
    "Piano_Sonata_No.32,_Op.111_(Beethoven,_Ludwig_van)",
    "https://imslp.org/wiki/Piano_Sonata_No.32,_Op.111_(Beethoven,_Ludwig_van)",
    WORK_PAGE,
)
SPARSE = (
    GoldComposer(entity_id="c2", label="Anonymous", known_imslp_url=None),
    "Fragment_(Anonymous)",
    "https://imslp.org/wiki/Fragment_(Anonymous)",
    WORK_PAGE_SPARSE,
)


def _stub_pages(monkeypatch: pytest.MonkeyPatch, pages: list[PageTuple]) -> None:
    def fake(gold_db_path: str, max_pages: int | None = None) -> Iterator[PageTuple]:
        yield from pages if max_pages is None else pages[:max_pages]

    monkeypatch.setattr("composer_scrapers.imslp_works.iter_work_pages", fake)


def test_imslp_works_is_registered() -> None:
    assert isinstance(REGISTRY["imslp_works"], ImslpWorksAdapter)
    assert REGISTRY["imslp_works"].name == "imslp_works"


def test_fetch_yields_a_mention_and_an_entity_per_work(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pages(monkeypatch, [SONATA])
    docs = list(ImslpWorksAdapter().fetch())

    assert len(docs) == 2
    mention, entity = docs
    assert isinstance(mention, WorkMentionDocument)
    assert isinstance(entity, EntityDocument)
    # Same source-local id (the page's full title): the two rows describe one work.
    assert mention.id == entity.id == "Piano Sonata No.32, Op.111 (Beethoven, Ludwig van)"
    assert entity.kind == "work"


def test_mention_title_has_the_composer_suffix_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pages(monkeypatch, [SONATA])
    mention = next(d for d in ImslpWorksAdapter().fetch() if isinstance(d, WorkMentionDocument))

    assert mention.title == "Piano Sonata No.32, Op.111"
    assert mention.composer == "Beethoven, Ludwig van"
    assert mention.raw["instrumentation"] == "piano"


def test_entity_label_keeps_the_composer_qualified_page_title(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare title would dedup two composers' same-named works into one entity."""
    _stub_pages(monkeypatch, [SONATA])
    entity = next(d for d in ImslpWorksAdapter().fetch() if isinstance(d, EntityDocument))
    assert entity.name == "Piano Sonata No.32, Op.111 (Beethoven, Ludwig van)"


def test_entity_claims_cover_composed_by_and_instrumentation(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pages(monkeypatch, [SONATA])
    entity = next(d for d in ImslpWorksAdapter().fetch() if isinstance(d, EntityDocument))
    claims = {claim.predicate: claim for claim in entity.claims}

    assert claims["composed_by"].object_kind == "person"
    assert claims["composed_by"].object_label == "Beethoven, Ludwig van"
    assert claims["has_scoring"].value == "piano"
    assert claims["composed_in"].value == "1821–22"
    assert claims["has_key"].value == "C minor"


def test_claims_are_omitted_for_fields_the_page_does_not_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pages(monkeypatch, [SPARSE])
    entity = next(d for d in ImslpWorksAdapter().fetch() if isinstance(d, EntityDocument))
    predicates = {claim.predicate for claim in entity.claims}

    assert "composed_by" in predicates
    assert "has_scoring" not in predicates
    assert "composed_in" not in predicates


def test_fetch_skips_pages_without_a_title(monkeypatch: pytest.MonkeyPatch) -> None:
    empty = (BEETHOVEN, "x", "https://imslp.org/wiki/x", "<html><body></body></html>")
    _stub_pages(monkeypatch, [empty])
    assert list(ImslpWorksAdapter().fetch()) == []


def test_fetch_passes_max_pages_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pages(monkeypatch, [SONATA, SPARSE])
    docs = list(ImslpWorksAdapter().fetch(max_pages=1))
    assert {d.id for d in docs} == {"Piano Sonata No.32, Op.111 (Beethoven, Ludwig van)"}
