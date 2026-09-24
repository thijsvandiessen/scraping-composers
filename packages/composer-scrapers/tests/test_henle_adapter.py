"""Tests for the Henle adapter: which documents a product becomes, and their claims.

The fetch layer is stubbed at the adapter's seams (sitemap and page sweep), so
these exercise only how parsed pages become documents.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

import pytest
from composer_schema import EntityDocument, SourceClaim, WorkMentionDocument
from composer_scrapers import REGISTRY
from composer_scrapers.henle import HenleAdapter, written_for
from test_henle import COLLECTION, SONATAS, WALTZ, Page, page

Doc = EntityDocument | WorkMentionDocument

COMPLETE_EDITION = page(
    Page(
        subtitle="Ludwig van Beethoven",
        title="Sect. 6, Vol. 3 | String Quartets I",
        properties=("Complete Edition, critical report, paperbound", "String Quartets"),
        contributors=("Paul Mies (Editor)", "Ernst Herttrich (Preface)"),
        number="4193",
    )
)
TIMER = page(Page(subtitle="", title="Henle Travel Timer", properties=(), contributors=()))
BOOK = page(
    Page(
        subtitle="Books & Periodicals",
        title="75 Jahre G. Henle Verlag",
        properties=("hardcover",),
        contributors=("Tobias Heyl (Editor)",),
        number="2675",
    )
)
PAGES = {
    "HN-659": WALTZ,
    "HN-1": SONATAS,
    "HN-134": COLLECTION,
    "HN-4193": COMPLETE_EDITION,
    "HN-9999": TIMER,
    "HN-2675": BOOK,
}


@pytest.fixture
def documents(monkeypatch: pytest.MonkeyPatch) -> list[Doc]:
    def iter_products(
        client: object,
        products: Iterable[tuple[str, str]],
        cache: object = None,
        max_pages: int | None = None,
    ) -> Iterator[tuple[str, str, str]]:
        for product_id, url in products:
            yield product_id, url, PAGES[product_id]

    urls = [(pid, f"https://www.henle.de/en/x/{pid}") for pid in PAGES]
    monkeypatch.setattr("composer_scrapers.henle.fetch_sitemap", lambda client: "")
    monkeypatch.setattr("composer_scrapers.henle.product_urls", lambda xml: urls)
    monkeypatch.setattr("composer_scrapers.henle.iter_products", iter_products)
    return list(HenleAdapter().fetch())


def _entity(docs: list[Doc], doc_id: str) -> EntityDocument:
    found = [doc for doc in docs if isinstance(doc, EntityDocument) and doc.id == doc_id]
    assert len(found) == 1
    return found[0]


def _mentions(docs: list[Doc]) -> dict[str, WorkMentionDocument]:
    return {doc.id: doc for doc in docs if isinstance(doc, WorkMentionDocument)}


def _edges(entity: EntityDocument, predicate: str) -> list[str | None]:
    return [claim.object_label for claim in entity.claims if claim.predicate == predicate]


def _values(entity: EntityDocument, predicate: str) -> list[str | None]:
    return [claim.value for claim in entity.claims if claim.predicate == predicate]


def test_the_adapter_is_registered() -> None:
    assert isinstance(REGISTRY["henle"], HenleAdapter)


def test_a_single_work_edition_is_one_work_with_every_claim(documents: list[Doc]) -> None:
    mention = _mentions(documents)["HN-659"]
    assert (mention.title, mention.composer) == ("Waltz a minor op. 34,2", "Frédéric Chopin")
    waltz = _entity(documents, "HN-659")
    assert waltz.name == "Waltz a minor op. 34,2 (Frédéric Chopin)"
    assert waltz.kind == "work"
    assert _values(waltz, "difficulty_level") == ["5"]
    assert _edges(waltz, "composed_by") == ["Frédéric Chopin"]
    assert _edges(waltz, "written_for") == ["piano"]
    assert _edges(waltz, "edited_by") == ["Ewald Zimmermann"]
    assert _edges(waltz, "fingering_by") == ["Hans-Martin Theopold"]
    assert _edges(waltz, "published_by") == ["G. Henle Verlag"]
    assert _values(waltz, "catalogue_number") == ["HN 659"]
    assert _values(waltz, "ismn") == ["979-0-2018-0659-4"]
    assert _values(waltz, "page_count") == ["7"]
    assert waltz.raw["title"] == "Waltz a minor op. 34 no. 2"


def test_a_volume_becomes_one_work_per_row_and_an_entity_for_itself(documents: list[Doc]) -> None:
    mentions = _mentions(documents)
    assert "HN-1" not in mentions, "the matcher must not look for a work called 'Piano Sonatas, Volume I'"
    assert mentions["HN-1#9"].title == "Piano Sonata a minor K. 310 (300d)"
    assert mentions["HN-1#9"].composer == "Wolfgang Amadeus Mozart"

    sonata = _entity(documents, "HN-1#9")
    assert sonata.name == "Piano Sonata a minor K. 310 (300d) (Wolfgang Amadeus Mozart)"
    assert _values(sonata, "difficulty_level") == ["7"]
    assert _edges(sonata, "part_of") == ["Piano Sonatas, Volume I (Wolfgang Amadeus Mozart)"]
    assert _edges(sonata, "written_for") == ["piano"]
    assert _edges(sonata, "edited_by") == [], "edition facts belong to the volume"
    assert sonata.raw["abrsm"] == ["Piano LRSM"]
    assert sonata.raw["band"] == "difficult"

    volume = _entity(documents, "HN-1")
    assert volume.name == "Piano Sonatas, Volume I (Wolfgang Amadeus Mozart)"
    assert _values(volume, "difficulty_level") == []
    assert _edges(volume, "edited_by") == ["Ewald Zimmermann"]


def test_an_anthology_credits_each_row_to_its_own_composer(documents: list[Doc]) -> None:
    minuet = _entity(documents, "HN-134#2")
    assert minuet.name == "Minuet (Nannerl Music Book) K. 1e (Wolfgang Amadeus Mozart)"
    assert _edges(minuet, "composed_by") == ["Wolfgang Amadeus Mozart"]
    assert _values(minuet, "difficulty_level") == ["1"]
    volume = _entity(documents, "HN-134")
    assert volume.name == "Easy Piano Pieces – Classical and Romantic Period, Volume I"
    assert _edges(volume, "composed_by") == []


def test_an_edition_without_contents_is_the_work_itself(documents: list[Doc]) -> None:
    mention = _mentions(documents)["HN-4193"]
    assert mention.title == "Sect. 6, Vol. 3 | String Quartets I"
    quartets = _entity(documents, "HN-4193")
    assert _edges(quartets, "written_for") == ["string quartet"]
    assert _edges(quartets, "includes_instrument") == ["violin", "viola", "cello"]
    assert _edges(quartets, "edited_by") == ["Paul Mies"], "a preface writer is not an editor"
    assert _values(quartets, "edition_type") == ["Complete Edition, critical report, paperbound"]


@pytest.mark.parametrize("product_id", ["HN-9999", "HN-2675"])
def test_a_product_that_is_not_sheet_music_yields_nothing(documents: list[Doc], product_id: str) -> None:
    """A book would otherwise arrive as a work by a composer called "Books &
    Periodicals" — 201 of the catalogue's products are books, gift items or info
    material."""
    assert not [doc for doc in documents if doc.id.startswith(product_id)]


@pytest.mark.parametrize(
    ("scoring", "expected"),
    [
        ("Piano solo", ("piano",)),
        ("Violin Concertos", ("violin", "orchestra")),
        ("Violoncello Concertos", ("cello", "orchestra")),
        ("Concerto", ()),
        ("Chamber music with winds", ()),
        (None, ()),
    ],
)
def test_written_for_reads_henle_scorings(scoring: str | None, expected: tuple[str, ...]) -> None:
    assert written_for(scoring) == expected


def test_claims_are_source_claims(documents: list[Doc]) -> None:
    for doc in documents:
        if isinstance(doc, EntityDocument):
            assert all(isinstance(claim, SourceClaim) for claim in doc.claims)
