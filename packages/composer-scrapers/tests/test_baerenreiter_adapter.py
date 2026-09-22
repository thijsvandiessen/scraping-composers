"""Tests for the Bärenreiter adapter: documents, claims and what is left out.

The fetch layer is stubbed at the adapter's seams (sitemap and product sweep), so
these exercise only how products become documents.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

import pytest
from composer_schema import EntityDocument, WorkMentionDocument
from composer_scrapers import REGISTRY
from composer_scrapers.baerenreiter import BaerenreiterAdapter, skipped_digital_twins
from test_baerenreiter import HIRE_WORK
from test_baerenreiter_instrumentation import ORCHESTRA

SYMPHONY: dict[str, Any] = {
    "id": "BA09001",
    "orderId": "BA 9001",
    "title": "Symphony No. 1",
    "artAuCompos": "Mahler, Gustav",
    "artAdmin": "Bärenreiter",
    "artPrTyDispl": "Score, Urtext edition",
    "artBstzMainDispl": "Orchestra",
    "artBstzDispl": ORCHESTRA,
    "artPagForm": "XII, 200 S. - 24,0 x 30,5 cm",
    "state": 0,
}
SYMPHONY_DIGITAL: dict[str, Any] = {**SYMPHONY, "id": "BA09001D", "digital": True, "state": 96}
DIGITAL_ONLY: dict[str, Any] = {"id": "BA07777D", "title": "Motet", "artAuCompos": "Schütz, Heinrich"}
BOOK: dict[str, Any] = {
    "id": "BVK01",
    "title": "Jahrbuch",
    "artPrTyDispl": "Book",
    "items": [{"title": "Vorwort"}],
}
ANTHOLOGY: dict[str, Any] = {
    "id": "BA08828",
    "title": "Opera Kaleidoscope for Soprano",
    "artPrTyDispl": "Vocal Score, Anthology, Urtext edition",
    "artBstzMainDispl": "Soprano, Piano",
    "items": [
        {"title": "Ombra mai fu", "titTiMain": "Ombra mai fu", "titAuCompos": "Händel, Georg Friedrich"},
        {"title": "Vorwort", "titTiMain": "Vorwort"},
        {
            "title": "Divinités du Styx",
            "titAuCompos": "Gluck, Christoph Willibald",
            "titBstz": "Soprano, Piano",
        },
    ],
}
CATALOGUE = [HIRE_WORK, SYMPHONY, SYMPHONY_DIGITAL, DIGITAL_ONLY, BOOK, ANTHOLOGY]


@pytest.fixture
def documents(monkeypatch: pytest.MonkeyPatch) -> list[EntityDocument | WorkMentionDocument]:
    by_id = {payload["id"]: payload for payload in CATALOGUE}
    requested: list[str] = []

    def iter_products(
        client: object, ids: Iterable[str], cache: object = None, max_pages: int | None = None
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        for product_id in ids:
            requested.append(product_id)
            yield product_id, by_id[product_id]

    monkeypatch.setattr("composer_scrapers.baerenreiter.fetch_sitemap", lambda client: "")
    monkeypatch.setattr("composer_scrapers.baerenreiter.product_ids", lambda xml: list(by_id))
    monkeypatch.setattr("composer_scrapers.baerenreiter.iter_products", iter_products)
    docs = list(BaerenreiterAdapter().fetch())
    assert "BA09001D" not in requested, "a digital twin must not even be fetched"
    return docs


def _entity(docs: list[EntityDocument | WorkMentionDocument], doc_id: str) -> EntityDocument:
    found = [doc for doc in docs if isinstance(doc, EntityDocument) and doc.id == doc_id]
    assert len(found) == 1
    return found[0]


def _claims(entity: EntityDocument, predicate: str) -> list[str]:
    return [
        claim.object_label or claim.value or "" for claim in entity.claims if claim.predicate == predicate
    ]


def test_registered() -> None:
    assert isinstance(REGISTRY["baerenreiter"], BaerenreiterAdapter)


def test_each_edition_is_a_work_mention_and_a_work_entity(
    documents: list[EntityDocument | WorkMentionDocument],
) -> None:
    mention = next(
        doc for doc in documents if isinstance(doc, WorkMentionDocument) and doc.id == "BA11625-72"
    )
    entity = _entity(documents, "BA11625-72")

    assert (mention.title, mention.composer) == ("So sieht’s aus", "Scartazzini, Andrea Lorenzo")
    assert mention.url == entity.url == "https://www.baerenreiter.com/en/product/BA11625-72"
    assert entity.kind == "work"
    assert entity.name == "So sieht’s aus (Scartazzini, Andrea Lorenzo)"


def test_the_requested_fields_are_claims_or_raw(
    documents: list[EntityDocument | WorkMentionDocument],
) -> None:
    entity = _entity(documents, "BA11625-72")

    assert _claims(entity, "composed_by") == ["Scartazzini, Andrea Lorenzo"]
    assert _claims(entity, "text_by") == ["Gomringer, Nora"]
    assert _claims(entity, "published_by") == ["Bärenreiter"]
    assert _claims(entity, "catalogue_number") == ["BA 11625-72"]
    assert _claims(entity, "composed_in") == ["2025"]
    assert _claims(entity, "has_duration") == ["11"]
    assert _claims(entity, "has_scoring") == ["Soprano solo, Strings"]
    assert _claims(entity, "edition_type") == ["Rental / hire material"]
    assert entity.raw["availability"] == "Hire material"
    assert entity.raw["remark"] == "Uraufführung 15.11.2025, Genf"
    assert entity.raw["fixed_retail_price"] is False
    assert str(entity.raw["manufacturer"]).startswith("Bärenreiter-Verlag")


def test_the_string_shorthand_is_the_instrumentation_when_nothing_else_states_it(
    documents: list[EntityDocument | WorkMentionDocument],
) -> None:
    entity = _entity(documents, "BA11625-72")
    instrumentation: Any = entity.raw["instrumentation"]

    assert _claims(entity, "written_for") == ["soprano", "strings"]
    assert _claims(entity, "includes_instrument") == ["violin", "viola", "cello", "double bass"]
    assert instrumentation["string_section"] == {
        "violin I": 4,
        "violin II": 4,
        "viola": 3,
        "cello": 2,
        "double bass": 1,
    }


def test_the_detail_list_becomes_instruments_and_counted_parts(
    documents: list[EntityDocument | WorkMentionDocument],
) -> None:
    entity = _entity(documents, "BA09001")
    parts: Any = entity.raw["instrumentation"]
    by_stated = {part["stated"]: part for part in parts["parts"]}

    assert _claims(entity, "written_for") == ["orchestra"]
    included = _claims(entity, "includes_instrument")
    assert {"piccolo", "flute", "horn", "bass drum", "cello"} <= set(included)
    assert len(included) == len(set(included))
    assert by_stated["Horn (4)"] == {"stated": "Horn (4)", "instrument": "horn", "count": 4}
    assert by_stated["Flute (2) (Piccolo flute)"]["also"] == ["piccolo"]
    assert parts["unmatched"] == []
    assert _claims(entity, "page_count") == ["200"]


def test_a_digital_twin_is_skipped_and_named_on_its_print_edition(
    documents: list[EntityDocument | WorkMentionDocument],
) -> None:
    assert not [doc for doc in documents if doc.id == "BA09001D"]
    assert _entity(documents, "BA09001").raw["digital_edition_id"] == "BA09001D"


def test_a_digital_only_edition_is_kept(documents: list[EntityDocument | WorkMentionDocument]) -> None:
    assert _entity(documents, "BA07777D").name == "Motet (Schütz, Heinrich)"


def test_a_book_yields_nothing(documents: list[EntityDocument | WorkMentionDocument]) -> None:
    assert not [doc for doc in documents if doc.id.startswith("BVK01")]


def test_an_anthology_yields_its_items_as_works_and_itself_only_as_an_entity(
    documents: list[EntityDocument | WorkMentionDocument],
) -> None:
    mentions = [
        doc for doc in documents if isinstance(doc, WorkMentionDocument) and doc.id.startswith("BA08828")
    ]

    assert [(m.id, m.title, m.composer) for m in mentions] == [
        ("BA08828#0", "Ombra mai fu", "Händel, Georg Friedrich"),
        ("BA08828#2", "Divinités du Styx", "Gluck, Christoph Willibald"),
    ]
    entity = _entity(documents, "BA08828")
    assert entity.name == "Opera Kaleidoscope for Soprano"
    assert _claims(entity, "composed_by") == []
    assert _claims(entity, "written_for") == ["soprano", "piano"]


def test_digital_twins_are_those_whose_print_id_is_listed() -> None:
    assert skipped_digital_twins(["BA1", "BA1D", "BA2D", "BA3-91", "BA3-91D"]) == {"BA1D", "BA3-91D"}
