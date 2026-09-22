"""Tests for reading one Bärenreiter shop-API product record."""

from __future__ import annotations

from typing import Any

import pytest
from composer_scrapers.baerenreiter.products import (
    duration_minutes,
    manufacturer_text,
    page_count,
    parse_product,
    people,
    product_types,
    twin_print_id,
)

# ``GET /api/bv/product/BA11625-72?lang=en``, verbatim but for the covers.
HIRE_WORK: dict[str, Any] = {
    "covers": [],
    "previews": [],
    "id": "BA11625-72",
    "artAdmin": "Bärenreiter",
    "artLength": "00:11:00",
    "artArtLangu": [],
    "artTextLangu": [],
    "artBstzOrchNum": "str (4.4.3.2.1)",
    "priFixRetP": False,
    "minAmount": 1,
    "state": 3,
    "digital": False,
    "price": 0.0,
    "priceDescription": "A.Anfr.",
    "orderId": "BA 11625-72",
    "artAuCompos": "Scartazzini, Andrea Lorenzo",
    "artAuText": "Gomringer, Nora",
    "artAnzeigeSuche": True,
    "title": "So sieht’s aus",
    "artTiMain": "So sieht’s aus",
    "artTiDatOr": "2025",
    "artTiSub": "Vier Lieder für Sopran und Streicher auf Gedichte von Nora Gomringer",
    "artRemarks": "Uraufführung 15.11.2025, Genf",
    "artPrTyDispl": "Rental / hire material",
    "artBstzMainDispl": "Soprano solo, Strings",
    "artGPSRManuf": "Bärenreiter-Verlag, Karl Vötterle GmbH &#38; Co. KG<br>Heinrich-Schütz-Allee 35-37, "
    "34131 Kassel, DE<br>info@baerenreiter.com",
}


def test_a_product_reads_into_the_fields_its_page_shows() -> None:
    product = parse_product(HIRE_WORK)

    assert product is not None
    assert product.id == "BA11625-72"
    assert product.edition_number == "BA 11625-72"
    assert product.title == "So sieht’s aus"
    assert product.composers == ("Scartazzini, Andrea Lorenzo",)
    assert product.librettists == ("Gomringer, Nora",)
    assert product.scoring == "Soprano solo, Strings"
    assert product.instrumentation_shorthand == "str (4.4.3.2.1)"
    assert product.composed_in == "2025"
    assert product.product_types == ("Rental / hire material",)
    assert product.remark == "Uraufführung 15.11.2025, Genf"
    assert product.duration_minutes == 11
    assert product.publisher == "Bärenreiter"
    assert product.availability == "Hire material"
    assert product.fixed_retail_price is False
    assert product.manufacturer == (
        "Bärenreiter-Verlag, Karl Vötterle GmbH & Co. KG\n"
        "Heinrich-Schütz-Allee 35-37, 34131 Kassel, DE\n"
        "info@baerenreiter.com"
    )
    assert product.is_music
    assert not product.is_anthology
    assert product.searchable


def test_a_sparse_record_leaves_its_fields_unset() -> None:
    product = parse_product({"id": "BA1", "title": "Sonata"})

    assert product is not None
    assert (product.composers, product.availability, product.duration_minutes) == ((), None, None)
    assert product.is_music


@pytest.mark.parametrize("payload", [{"id": "BA1"}, {"title": "Sonata"}, {"id": "BA1", "title": "  "}])
def test_a_record_without_id_or_title_is_not_a_product(payload: dict[str, Any]) -> None:
    assert parse_product(payload) is None


def test_books_and_magazines_are_not_sheet_music() -> None:
    book = parse_product({"id": "BVK1", "title": "Jahrbuch", "artPrTyDispl": "Book"})
    anthology = parse_product(
        {"id": "BA2", "title": "Arias", "artPrTyDispl": "Vocal Score, Anthology, Urtext edition"}
    )

    assert book is not None and not book.is_music
    assert anthology is not None and anthology.is_music and anthology.is_anthology


def test_a_credit_naming_several_people_is_split() -> None:
    assert people("Kühn, Clemens / Gervink, Manuel") == ("Kühn, Clemens", "Gervink, Manuel")
    assert people("Beethoven, Ludwig van / Czerny, Carl et al.") == ("Beethoven, Ludwig van", "Czerny, Carl")
    assert people(None) == ()


@pytest.mark.parametrize(
    ("stated", "minutes"),
    [("00:11:00", 11), ("01:05:40", 66), ("00:00:20", None), ("11'", None), (None, None)],
)
def test_duration_is_whole_minutes(stated: str | None, minutes: int | None) -> None:
    assert duration_minutes(stated) == minutes


@pytest.mark.parametrize(
    ("stated", "pages"),
    [("XV, 45 S. - 24,0 x 30,5 cm", 45), ("4 S.", 4), ("IV, 32/10/10 S.", None), (None, None)],
)
def test_page_count_is_read_only_when_one_number_states_it(stated: str | None, pages: int | None) -> None:
    assert page_count(stated) == pages


def test_product_format_is_split_and_loses_its_item_counts() -> None:
    assert product_types("Performance score (3), Urtext edition") == ("Performance score", "Urtext edition")


def test_a_digital_id_names_its_print_twin() -> None:
    assert twin_print_id("BA05163D") == "BA05163"
    assert twin_print_id("BA00692-91D") == "BA00692-91"
    assert twin_print_id("BA05163") is None


def test_manufacturer_text_survives_missing_input() -> None:
    assert manufacturer_text(None) is None
    assert manufacturer_text("<br>") is None


@pytest.mark.parametrize(("state", "label"), [(0, "Available"), (96, "Digital edition"), (93, None)])
def test_availability_is_named_only_where_the_site_names_it(state: int, label: str | None) -> None:
    product = parse_product({"id": "BA1", "title": "T", "state": state})

    assert product is not None and product.availability == label
