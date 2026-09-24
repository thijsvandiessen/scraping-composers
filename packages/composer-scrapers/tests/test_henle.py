"""Tests for reading one henle.de product page.

The pages below are cut down from real ones (HN 659, HN 1, HN 134), keeping the
markup around every field the parser reads and a navigation flyout that states a
scoring of its own, which must not leak onto the product.
"""

from __future__ import annotations

from dataclasses import dataclass

from composer_scrapers.henle.products import ContentsItem, Contributor, parse_product

NAVIGATION = """
<div class="col-auto flyout-navlist-root-item">
  <a href="https://www.henle.de/en/Organ/" class="flyout-instrumentation-parentcategory-a">
    <span itemprop="name">Organ</span></a>
</div>
<div class="product-detail-properties-container"><span>Organ</span></div>
"""

CONTENTS_HEADER = """
<div class="cms-element-title-works " >
  <div class="mws-henle-work is-header" >
    <div class="mws-henle-work--title"></div>
    <div class="mws-henle-work--grade"> Difficulty <br />
      <a href="https://www.henle.de/en/Levels-of-Difficulty/" title="Explanation"> (Explanation) </a>
    </div>
    <div class="mws-henle-work--action">Other titles of this difficulty</div>
  </div>
"""
#: The CMS section after the contents; the cross-selling box lives past it.
AFTER_CONTENTS = """
</div>
<div class="cms-section pos-2 cms-section-default">
  <div class="mws-henle-work " data-item="1"><div class="mws-henle-work--title ">Not this</div></div>
</div>
"""


@dataclass(frozen=True)
class Row:
    """One contents row; ``grade`` as the page shows it, "5 medium"."""

    index: int
    title: str
    grade: str = ""
    composer: str = ""
    abrsm: str = ""
    heading: bool = False


@dataclass(frozen=True)
class Page:
    """The fields of a product page, defaulting to HN 659's."""

    subtitle: str = "Frédéric Chopin"
    title: str = "Waltz a minor op. 34 no. 2"
    contributors: tuple[str, ...] = ("Ewald Zimmermann (Editor)", "Hans-Martin Theopold (Fingering Piano)")
    properties: tuple[str, ...] = ("Urtext Edition, paperbound", "Piano solo")
    pages: str = "Pages 7 (II+5), Size 23,5 x 31,0 cm"
    number: str = "659"
    variants: tuple[str, ...] = ("Digital (tablet)",)
    rows: tuple[Row, ...] = ()


def _row(row: Row) -> str:
    name = f'<i class="title-works-composername-i">{row.composer}</i><br />' if row.composer else ""
    exams = (
        '<div class="mws-henle-work--abrsm-container"><div><span>ABRSM:</span></div>'
        '<div class="mws-henle-work--abrsm-content"><div>'
        f'<a href="https://www.henle.de/en/ABRSM-Grades/" title="ABRSM"> {row.abrsm} </a>'
        "</div></div></div>"
        if row.abrsm
        else ""
    )
    graded = ""
    if row.grade:
        number, band = row.grade.split()
        graded = f'<span class="mws-henle-work--grade-numeric">{number}</span> <span>{band}</span>'
    return f"""
  <div class="mws-henle-work " data-item="{row.index}" >
    <div class="mws-henle-work--title {"bold" if row.heading else ""}"> {name}{row.title} </div>
    <div class="mws-henle-work--grade"> {graded} {exams} </div>
    <div class="mws-henle-work--action"><a class="nav-link" href="#"><button></button></a></div>
  </div>"""


def page(fields: Page) -> str:
    credit_rows = "".join(
        f'<div class="product-persons-contributors-row"><a class="product-persons-url '
        f'product-persons-contributors-person-url" href="#"> {credit}</a></div>'
        for credit in fields.contributors
    )
    property_rows = "".join(
        f'<div class="product-detail-properties-container"><span>{value}</span></div>'
        for value in fields.properties
    )
    variant_links = "".join(
        f'<a href="#" class="btn product-detail-configurator-option-label is-display-text" '
        f'title="{label}">{label}</a>'
        for label in fields.variants
    )
    rows = "".join(_row(row) for row in fields.rows)
    return f"""<html><body>{NAVIGATION}
<div class="buybox_product_name_wrapper">
  <h2 class="product-detail-subtitle"> {fields.subtitle} </h2>
  <h1 class="product-detail-name" itemprop="name"> {fields.title} </h1>
</div>
<div class="product-persons-list"><div class="product-persons-contributors">{credit_rows}
  <div class="product-persons-contributors-row"><span> &nbsp; </span></div>
</div></div>
<!-- PRODUCT DETAILS -->
{property_rows}
<div class="product-detail-weight-container">
  <span class="product-detail-weight" itemprop="weight"> {fields.pages} </span>
</div>
<div class="product-detail-weight-container">
  <span class="product-detail-weight" itemprop="weight"> Weight 56 g </span>
</div>
<div class="product-detail-hn-ismn-container">
  <span class="product-detail-ordernumber" itemprop="sku"> HN {fields.number} </span> ·
  <meta itemprop="ISMN" content="979-0-2018-0{fields.number}-4"/>
</div>
<div class="product-detail-configurator-container">{variant_links}</div>
<p class="cms-section-werbetext-bio">Chopin applied himself the waltz genre&nbsp;throughout his life.</p>
<div class="henle_buybox_action_wrapper col-lg-5">
  <div class="product-detail-properties-container"><span>Harp solo</span></div>
</div>
{CONTENTS_HEADER}{rows}{AFTER_CONTENTS}
</body></html>"""


WALTZ = page(Page(rows=(Row(1, "Waltz a minor op. 34,2", "5 medium"),)))
SONATAS = page(
    Page(
        subtitle="Wolfgang Amadeus Mozart",
        title="Piano Sonatas, Volume I",
        number="001",
        variants=("HN1", "HN1001", "HN9001", "Digital (tablet)"),
        rows=(
            Row(1, "Piano Sonata C major K. 279 (189d)", "6 medium"),
            Row(9, "Piano Sonata a minor K. 310 (300d)", "7 difficult", abrsm="Piano LRSM"),
        ),
    )
)
COLLECTION = page(
    Page(
        subtitle="Piano Music (Collection)",
        title="Easy Piano Pieces – Classical and Romantic Period, Volume I",
        rows=(
            Row(
                1,
                "11 Pieces from &quot;60 Pieces for the Beginning Pianist&quot;",
                "1 easy",
                "Daniel Gottlob Türk",
            ),
            Row(2, "Minuet (Nannerl Music Book) K. 1e", "1 easy", "Wolfgang Amadeus Mozart"),
        ),
    )
)


def test_the_header_fields_are_read_by_their_markup() -> None:
    product = parse_product("HN-659", WALTZ)
    assert product is not None
    assert product.title == "Waltz a minor op. 34 no. 2"
    assert product.composers == ("Frédéric Chopin",)
    assert product.edition_number == "HN 659"
    assert product.ismn == "979-0-2018-0659-4"
    assert product.page_count == 7
    assert product.format == "Pages 7 (II+5), Size 23,5 x 31,0 cm"
    assert product.variants == ("Digital (tablet)",)
    assert product.description == "Chopin applied himself the waltz genre throughout his life."


def test_the_edition_type_and_the_scoring_are_told_apart() -> None:
    """Both sit in identical containers; the navigation and the buy box hold more
    of them ("Organ", "Harp solo"), which must not land on the product."""
    product = parse_product("HN-659", WALTZ)
    assert product is not None
    assert product.edition_type == "Urtext Edition, paperbound"
    assert product.scoring == "Piano solo"


def test_contributors_keep_every_role_and_drop_the_blank_row() -> None:
    product = parse_product(
        "HN-4193", page(Page(contributors=("Nancy November (Editor, Preface)", "Ernst Herttrich (Preface)")))
    )
    assert product is not None
    assert product.contributors == (
        Contributor("Nancy November", ("Editor", "Preface")),
        Contributor("Ernst Herttrich", ("Preface",)),
    )


def test_each_contents_row_carries_its_grade_and_band() -> None:
    product = parse_product("HN-1", SONATAS)
    assert product is not None
    assert product.works == (
        ContentsItem(1, "Piano Sonata C major K. 279 (189d)", grade=6, band="medium"),
        ContentsItem(
            9, "Piano Sonata a minor K. 310 (300d)", grade=7, band="difficult", abrsm=("Piano LRSM",)
        ),
    )


def test_a_set_is_a_bold_heading_row_before_its_pieces() -> None:
    product = parse_product(
        "HN-4",
        page(
            Page(
                rows=(
                    Row(1, "4 Impromptus op. 90 D 899", heading=True),
                    Row(2, "Impromptu c minor op. 90,1 D 899", "6 medium"),
                )
            )
        ),
    )
    assert product is not None
    assert product.works == (
        ContentsItem(1, "4 Impromptus op. 90 D 899", heading=True),
        ContentsItem(2, "Impromptu c minor op. 90,1 D 899", grade=6, band="medium"),
    )


def test_an_ungraded_row_still_names_its_work() -> None:
    """Henle grades five instruments only; a quartet's row has no grade."""
    product = parse_product(
        "HN-741", page(Page(properties=("String Quartets",), rows=(Row(1, "String Quartet op. 130"),)))
    )
    assert product is not None
    assert product.works == (ContentsItem(1, "String Quartet op. 130"),)
    assert product.scoring == "String Quartets"
    assert product.edition_type is None


def test_an_anthology_names_no_composer_and_its_rows_name_their_own() -> None:
    product = parse_product("HN-134", COLLECTION)
    assert product is not None
    assert product.is_collection
    assert product.composers == ()
    assert [(item.composer, item.title, item.grade) for item in product.works] == [
        ("Daniel Gottlob Türk", '11 Pieces from "60 Pieces for the Beginning Pianist"', 1),
        ("Wolfgang Amadeus Mozart", "Minuet (Nannerl Music Book) K. 1e", 1),
    ]


def test_several_composers_are_split() -> None:
    product = parse_product("HN-2", page(Page(subtitle="Joseph Haydn / Ludwig van Beethoven")))
    assert product is not None
    assert product.composers == ("Joseph Haydn", "Ludwig van Beethoven")


def test_a_score_with_parts_states_no_single_page_count() -> None:
    product = parse_product("HN-3", page(Page(pages="Pages 28/12/12/10, Size 23,5 x 31,0 cm")))
    assert product is not None
    assert product.page_count is None


def test_a_book_states_no_scoring_and_no_contents() -> None:
    """A book's series sits where a composer's name goes, so the credit cannot
    tell the two apart; the scoring and the contents can."""
    product = parse_product(
        "HN-2675",
        page(
            Page(subtitle="Books & Periodicals", title="75 Jahre G. Henle Verlag", properties=("hardcover",))
        ),
    )
    assert product is not None
    assert not product.is_music
    assert product.edition_type == "hardcover"
    assert product.scoring is None


def test_a_page_without_a_product_is_none() -> None:
    assert parse_product("HN-0", "<html><body><h1>Not found</h1></body></html>") is None
