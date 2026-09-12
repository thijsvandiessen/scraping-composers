"""Tests for the ImslpWorksAdapter's document output.

The pairing matters: the warehouse routes WorkMentionDocument and
EntityDocument down different paths (canonical works vs entities + claims),
so an IMSLP work has to arrive as both to be fully represented — the same
reasoning ``test_boosey_adapter.py`` documents for its own source.

The other thing under test here is that the catalogue does not depend on the
detail pass. A work IMSLP lists is reported whether or not its page was read;
reading the page only adds claims.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from composer_schema import EntityDocument, WorkMentionDocument
from composer_scrapers import REGISTRY
from composer_scrapers.imslp_works import ImslpWorksAdapter
from composer_scrapers.imslp_works.fetch import WorkRow
from test_imslp_works import WORK_PAGE, WORK_PAGE_SPARSE

Result = tuple[WorkRow, str | None]

SONATA_ROW = WorkRow(
    page_id=1,
    title="Piano Sonata No.32, Op.111 (Beethoven, Ludwig van)",
    composer="Beethoven, Ludwig van",
    catalogue_number="ILB 193",
    url="https://imslp.org/wiki/Piano_Sonata_No.32,_Op.111_(Beethoven,_Ludwig_van)",
)
SPARSE_ROW = WorkRow(
    page_id=2,
    title="Fragment (Anonymous)",
    composer="Anonymous",
    catalogue_number=None,
    url="https://imslp.org/wiki/Fragment_(Anonymous)",
)

SONATA: Result = (SONATA_ROW, WORK_PAGE)
SPARSE: Result = (SPARSE_ROW, WORK_PAGE_SPARSE)
UNREAD: Result = (SPARSE_ROW, None)


def _stub(monkeypatch: pytest.MonkeyPatch, results: list[Result]) -> None:
    def fake(max_details: int | None = None) -> Iterator[Result]:
        for index, result in enumerate(results):
            yield result if max_details is None or index < max_details else (result[0], None)

    monkeypatch.setattr("composer_scrapers.imslp_works.iter_works", fake)


def _entity(docs: list[EntityDocument | WorkMentionDocument]) -> EntityDocument:
    return next(d for d in docs if isinstance(d, EntityDocument))


def _mention(docs: list[EntityDocument | WorkMentionDocument]) -> WorkMentionDocument:
    return next(d for d in docs if isinstance(d, WorkMentionDocument))


def test_imslp_works_is_registered() -> None:
    assert isinstance(REGISTRY["imslp_works"], ImslpWorksAdapter)
    assert REGISTRY["imslp_works"].name == "imslp_works"


def test_fetch_yields_a_mention_and_an_entity_per_work(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, [SONATA])
    docs = list(ImslpWorksAdapter().fetch())

    assert len(docs) == 2
    mention, entity = docs
    assert isinstance(mention, WorkMentionDocument)
    assert isinstance(entity, EntityDocument)
    # Same source-local id (the page's full title): the two rows describe one work.
    assert mention.id == entity.id == "Piano Sonata No.32, Op.111 (Beethoven, Ludwig van)"
    assert entity.kind == "work"


def test_mention_title_has_the_composer_suffix_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, [SONATA])
    mention = _mention(list(ImslpWorksAdapter().fetch()))

    assert mention.title == "Piano Sonata No.32, Op.111"
    assert mention.composer == "Beethoven, Ludwig van"
    assert mention.raw["instrumentation"] == "piano"


def test_entity_label_keeps_the_composer_qualified_page_title(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare title would dedup two composers' same-named works into one entity."""
    _stub(monkeypatch, [SONATA])
    assert _entity(list(ImslpWorksAdapter().fetch())).name == (
        "Piano Sonata No.32, Op.111 (Beethoven, Ludwig van)"
    )


def test_entity_claims_cover_composed_by_and_instrumentation(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, [SONATA])
    claims = {claim.predicate: claim for claim in _entity(list(ImslpWorksAdapter().fetch())).claims}

    assert claims["composed_by"].object_kind == "person"
    assert claims["composed_by"].object_label == "Beethoven, Ludwig van"
    assert claims["has_scoring"].value == "piano"
    assert claims["composed_in"].value == "1821–22"
    assert claims["has_key"].value == "C minor"


def test_the_worklists_catalogue_number_is_claimed(monkeypatch: pytest.MonkeyPatch) -> None:
    """It comes free with the bulk row — no page needs reading for it."""
    _stub(monkeypatch, [SONATA])
    claims = {claim.predicate: claim for claim in _entity(list(ImslpWorksAdapter().fetch())).claims}
    assert claims["catalogue_number"].value == "ILB 193"


def test_claims_are_omitted_for_fields_the_page_does_not_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, [SPARSE])
    predicates = {claim.predicate for claim in _entity(list(ImslpWorksAdapter().fetch())).claims}

    assert "composed_by" in predicates
    assert "has_scoring" not in predicates
    assert "composed_in" not in predicates


class TestUnenrichedWorks:
    """A work whose detail page was never fetched is still a work."""

    def test_it_is_still_reported_as_both_documents(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub(monkeypatch, [UNREAD])
        docs = list(ImslpWorksAdapter().fetch())
        assert [type(doc) for doc in docs] == [WorkMentionDocument, EntityDocument]
        assert _mention(docs).composer == "Anonymous"

    def test_it_still_claims_what_the_worklist_stated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub(monkeypatch, [UNREAD])
        predicates = {claim.predicate for claim in _entity(list(ImslpWorksAdapter().fetch())).claims}
        assert predicates == {"composed_by"}

    def test_the_payload_says_nobody_looked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Distinguishes a work with no scoring from one nobody has read."""
        _stub(monkeypatch, [UNREAD, SONATA])
        mentions = [d for d in ImslpWorksAdapter().fetch() if isinstance(d, WorkMentionDocument)]
        assert [m.raw["enriched"] for m in mentions] == [False, True]


def test_max_pages_bounds_enrichment_not_the_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Capping the detail pass must never cost a work its row."""
    _stub(monkeypatch, [SONATA, SPARSE])
    docs = list(ImslpWorksAdapter().fetch(max_pages=1))

    assert {doc.id for doc in docs} == {
        "Piano Sonata No.32, Op.111 (Beethoven, Ludwig van)",
        "Fragment (Anonymous)",
    }
    mentions = [d for d in docs if isinstance(d, WorkMentionDocument)]
    assert [m.raw["enriched"] for m in mentions] == [True, False]
