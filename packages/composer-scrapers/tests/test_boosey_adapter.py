"""Tests for the Boosey adapter's document output.

The pairing matters: the warehouse routes WorkMentionDocument and
EntityDocument down different paths (canonical works vs entities + claims), so a
Boosey work has to arrive as both to be fully represented.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from composer_schema import EntityDocument, WorkMentionDocument
from composer_scrapers import REGISTRY
from composer_scrapers.boosey import BooseyAdapter
from composer_scrapers.boosey.catalogue import WorkLink
from test_boosey import WORK_PAGE, WORK_PAGE_SPARSE

PageTuple = tuple[WorkLink, str, str]


def _stub_pages(monkeypatch: pytest.MonkeyPatch, pages: list[PageTuple]) -> None:
    def fake(max_pages: int | None = None) -> Iterator[PageTuple]:
        yield from pages if max_pages is None else pages[:max_pages]

    monkeypatch.setattr("composer_scrapers.boosey.iter_work_pages", fake)


KERORI = (WorkLink("27637", "/cr/music/Walter-Steffens-Kerori/27637"), "https://www.boosey.com/x", WORK_PAGE)
SPARSE = (WorkLink("1", "/cr/music/a/1"), "https://www.boosey.com/a", WORK_PAGE_SPARSE)


def test_boosey_is_registered() -> None:
    assert isinstance(REGISTRY["boosey"], BooseyAdapter)
    assert REGISTRY["boosey"].name == "boosey"


def test_fetch_yields_a_mention_and_an_entity_per_work(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pages(monkeypatch, [KERORI])
    docs = list(BooseyAdapter().fetch())

    assert len(docs) == 2
    mention, entity = docs
    assert isinstance(mention, WorkMentionDocument)
    assert isinstance(entity, EntityDocument)
    # Same source-local id: the two rows describe one work.
    assert mention.id == entity.id == "27637"
    assert entity.kind == "work"


def test_mention_carries_title_composer_and_full_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pages(monkeypatch, [KERORI])
    mention = next(d for d in BooseyAdapter().fetch() if isinstance(d, WorkMentionDocument))

    assert mention.title == "Kerori"
    assert mention.composer == "Walter Steffens"
    assert mention.raw["duration_minutes"] == 12
    assert mention.raw["scoring"].startswith("2(II=picc)")
    assert mention.raw["url"] == "https://www.boosey.com/x"


def test_entity_label_is_composer_qualified(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare title would dedup two composers' same-named works into one entity."""
    _stub_pages(monkeypatch, [KERORI])
    entity = next(d for d in BooseyAdapter().fetch() if isinstance(d, EntityDocument))
    assert entity.name == "Kerori (Walter Steffens)"


def test_entity_claims_cover_composer_scoring_duration_and_year(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_pages(monkeypatch, [KERORI])
    entity = next(d for d in BooseyAdapter().fetch() if isinstance(d, EntityDocument))
    claims = {claim.predicate: claim for claim in entity.claims}

    assert claims["composed_by"].object_kind == "person"
    assert claims["composed_by"].object_label == "Walter Steffens"
    assert claims["published_by"].object_kind == "publisher"
    assert claims["has_duration"].value == "12"
    assert claims["composed_in"].value == "1998"
    assert (claims["has_scoring"].value or "").endswith("timp - str")


def test_claims_are_omitted_for_fields_the_page_does_not_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_pages(monkeypatch, [SPARSE])
    entity = next(d for d in BooseyAdapter().fetch() if isinstance(d, EntityDocument))
    predicates = {claim.predicate for claim in entity.claims}

    assert "composed_in" in predicates
    assert "has_duration" not in predicates
    assert "has_scoring" not in predicates


def test_fetch_skips_pages_without_a_title(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pages(monkeypatch, [(WorkLink("9", "/cr/music/x/9"), "https://www.boosey.com/x", "<html></html>")])
    assert list(BooseyAdapter().fetch()) == []


def test_fetch_passes_max_pages_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pages(monkeypatch, [KERORI, SPARSE])
    docs = list(BooseyAdapter().fetch(max_pages=1))
    assert {d.id for d in docs} == {"27637"}
