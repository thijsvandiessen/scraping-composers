"""One henle.de product page, read into named fields.

The page is a Shopware template, and the parts worth reading carry class names
that say what they hold:

- ``h2.product-detail-subtitle`` — the composer, or where the product is not one
  person's music, the shelf it sits on: "Piano Music (Collection)" for an
  anthology, "Books & Periodicals" or "Haydn Studies" for a book;
- ``h1.product-detail-name`` — the edition's title;
- ``.product-persons-contributors-row`` — "Ewald Zimmermann (Editor)", one
  contributor per row, the roles in the parenthesis;
- ``.product-detail-properties-container`` — two short facts, the edition type
  ("Urtext Edition, paperbound") and the scoring ("Piano solo");
- ``itemprop="sku"`` / ``itemprop="ISMN"`` — "HN 659" and the ISMN;
- ``.mws-henle-work`` — the contents, one row per work, with the work's own title
  (and, in an anthology, its composer) and Henle's **level of difficulty**: a
  grade from 1 to 9 and its band (easy / medium / difficult). Henle grades
  piano, violin, flute, cello and clarinet music only, so a quartet's row has a
  title and no grade. A set is a bold row ("4 Impromptus op. 90 D 899"),
  ungraded, followed by its graded pieces as plain rows.

Everything outside those — the navigation (which itself lists "Piano solo" and
every other scoring), biographies, cross-selling boxes — is cut away before
anything is read, so a stray match there cannot land on the product.

Titles are passed through exactly as the page gives them: the work matcher reads
an opus number in a title as near-proof of identity (see ``boosey/works.py``).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")

_SUBTITLE = re.compile(r'<h2 class="product-detail-subtitle">(.*?)</h2>', re.DOTALL)
_NAME = re.compile(r'<h1 class="product-detail-name"[^>]*>(.*?)</h1>', re.DOTALL)
_CONTRIBUTOR = re.compile(r'<div class="product-persons-contributors-row">(.*?)</div>', re.DOTALL)
_PROPERTY = re.compile(r'<div class="product-detail-properties-container">(.*?)</div>', re.DOTALL)
_WEIGHT = re.compile(r'<span class="product-detail-weight"[^>]*>(.*?)</span>', re.DOTALL)
_SKU = re.compile(r'<span class="product-detail-ordernumber"[^>]*>(.*?)</span>', re.DOTALL)
_ISMN = re.compile(r'<meta itemprop="ISMN" content="([^"]*)"')
_VARIANT = re.compile(r'product-detail-configurator-option-label[^>]*title="([^"]*)"')
_DESCRIPTION = re.compile(r'<p class="cms-section-werbetext-bio">(.*?)</p>', re.DOTALL)

#: "Hans-Martin Theopold (Fingering Piano)", "Nancy November (Editor, Preface)".
_CREDIT = re.compile(r"^(?P<name>.+?)\s*\((?P<roles>[^()]+)\)$")
#: "Pages 7 (II+5), Size 23,5 x 31,0 cm". Only a single count is a page count; a
#: score-and-parts product may state several, which no one number sums up.
_PAGES = re.compile(r"\bPages\s+(\d+)\b(?!\s*/)")

#: A contents row starts at its own div; the header row has no ``data-item``.
_ROW_START = re.compile(r'<div class="mws-henle-work\b[^"]*"[^>]*\bdata-item="(\d+)"')
_ROW_TITLE = re.compile(r'<div class="mws-henle-work--title([^"]*)">(.*?)</div>', re.DOTALL)
_ROW_COMPOSER = re.compile(r'<i class="title-works-composername-i">(.*?)</i>', re.DOTALL)
_ROW_GRADE = re.compile(
    r'<span class="mws-henle-work--grade-numeric">\s*(\d+)\s*</span>\s*<span>(.*?)</span>', re.DOTALL
)
_ROW_ABRSM = re.compile(r'<div class="mws-henle-work--abrsm-content">(.*?)</div>\s*</div>', re.DOTALL)
_LINK_TEXT = re.compile(r"<a\b[^>]*>(.*?)</a>", re.DOTALL)

#: Where the product header starts and ends: the title block through the buy box
#: text, before the purchase form and everything after it.
_HEADER_START = "buybox_product_name_wrapper"
_HEADER_END = "henle_buybox_action_wrapper"
_CONTENTS_START = "cms-element-title-works"
#: The contents block is one CMS section; the next section ends it.
_CONTENTS_END = '<div class="cms-section '

#: An anthology's subtitle names a repertoire, not a person: "Piano Music
#: (Collection)". Its rows name their own composers.
_COLLECTION = re.compile(r"\(Collection\)\s*$", re.IGNORECASE)
#: Several composers are written "A / B".
_COMPOSER_SEPARATOR = " / "

#: Words that mark the edition-type property ("Urtext Edition, paperbound",
#: "Study Edition", "Set of parts") and never occur in a scoring ("Piano solo",
#: "String Quartets").
_EDITION_WORDS = re.compile(
    r"\b(?:edition|paperbound|clothbound|hardcover|softcover|paperback|bound|score|parts|slipcase|facsimile)\b",
    re.I,
)


def _text(fragment: str) -> str:
    """Markup reduced to its visible text, entities decoded, whitespace folded."""
    return _SPACES.sub(" ", html.unescape(_TAG.sub(" ", fragment))).replace("\xa0", " ").strip()


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return (_text(match.group(1)) or None) if match is not None else None


@dataclass(frozen=True)
class Contributor:
    """A credit on the edition: who, and in which roles ("Editor", "Fingering Piano")."""

    name: str
    roles: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "roles": list(self.roles)}


@dataclass(frozen=True)
class ContentsItem:
    """One row of the edition's contents: a work, and how hard Henle rates it."""

    index: int
    title: str
    #: The row's own composer, stated only in an anthology.
    composer: str | None = None
    #: Henle's level of difficulty, 1 (easiest) to 9.
    grade: int | None = None
    #: The grade's band as the page words it: "easy", "medium", "difficult".
    band: str | None = None
    #: ABRSM exam grades the work is set for ("Piano ARSM").
    abrsm: tuple[str, ...] = ()
    #: Set in bold: a set of pieces ("4 Impromptus op. 90 D 899") whose pieces
    #: usually follow as rows of their own. Usually — nothing in the markup says
    #: where the set ends, so the rows are not nested here.
    heading: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "title": self.title,
            "composer": self.composer,
            "grade": self.grade,
            "band": self.band,
            "abrsm": list(self.abrsm),
            "heading": self.heading,
        }


@dataclass(frozen=True)
class ParsedProduct:
    """A Henle edition, under the labels its product page gives its fields."""

    id: str
    title: str
    #: The order number as printed, "HN 659".
    edition_number: str | None = None
    #: The subtitle as the page writes it: a composer, or "Piano Music (Collection)".
    subtitle: str | None = None
    composers: tuple[str, ...] = ()
    contributors: tuple[Contributor, ...] = ()
    #: "Urtext Edition, paperbound".
    edition_type: str | None = None
    #: "Piano solo", "String Quartets", "Violin and Piano".
    scoring: str | None = None
    #: The "Pages …, Size …" line, verbatim.
    format: str | None = None
    page_count: int | None = None
    ismn: str | None = None
    #: The other editions of this title the page offers ("HN9741", "Digital (tablet)").
    variants: tuple[str, ...] = ()
    description: str | None = None
    works: tuple[ContentsItem, ...] = ()

    @property
    def is_collection(self) -> bool:
        return self.subtitle is not None and _COLLECTION.search(self.subtitle) is not None

    @property
    def is_music(self) -> bool:
        """Whether this is sheet music at all.

        The shop also sells books, source catalogues, gift items and info
        material, and those state neither a scoring nor any contents — across the
        catalogue, 201 products state neither and not one of them is music. The
        composer credit is no test: a book is filed under its series ("Books &
        Periodicals", "Haydn Studies"), which the page writes exactly where a
        composer's name goes."""
        return bool(self.scoring or self.works)


def _region(page: str, start: str, end: str) -> str:
    begin = page.find(start)
    if begin < 0:
        return ""
    finish = page.find(end, begin)
    return page[begin : finish if finish >= 0 else len(page)]


def _contributor(row: str) -> Contributor | None:
    text = _text(row)
    if not text:
        return None
    if (match := _CREDIT.match(text)) is None:
        return Contributor(text)
    roles = tuple(role.strip() for role in match.group("roles").split(",") if role.strip())
    return Contributor(match.group("name").strip(), roles)


def _composers(subtitle: str | None) -> tuple[str, ...]:
    if subtitle is None or _COLLECTION.search(subtitle):
        return ()
    return tuple(name.strip() for name in subtitle.split(_COMPOSER_SEPARATOR) if name.strip())


def _row(index: int, chunk: str) -> ContentsItem | None:
    title_match = _ROW_TITLE.search(chunk)
    if title_match is None:
        return None
    heading = "bold" in title_match.group(1).split()
    title_html = title_match.group(2)
    composer = _first(_ROW_COMPOSER, title_html)
    title = _text(_ROW_COMPOSER.sub("", title_html))
    if not title:
        return None
    grade = band = None
    if (graded := _ROW_GRADE.search(chunk)) is not None:
        grade, band = int(graded.group(1)), _text(graded.group(2)) or None
    abrsm: tuple[str, ...] = ()
    if (exams := _ROW_ABRSM.search(chunk)) is not None:
        abrsm = tuple(text for link in _LINK_TEXT.findall(exams.group(1)) if (text := _text(link)))
    return ContentsItem(index, title, composer, grade, band, abrsm, heading)


def parse_contents(block: str) -> tuple[ContentsItem, ...]:
    """The contents rows of *block*, in page order."""
    starts = list(_ROW_START.finditer(block))
    rows: list[ContentsItem] = []
    for position, start in enumerate(starts):
        end = starts[position + 1].start() if position + 1 < len(starts) else len(block)
        if (row := _row(int(start.group(1)), block[start.start() : end])) is not None:
            rows.append(row)
    return tuple(rows)


def parse_product(product_id: str, page: str) -> ParsedProduct | None:
    """The product *page* describes, or ``None`` when it names no product."""
    header = _region(page, _HEADER_START, _HEADER_END)
    title = _first(_NAME, header)
    if title is None:
        return None
    subtitle = _first(_SUBTITLE, header)
    properties = [text for fragment in _PROPERTY.findall(header) if (text := _text(fragment))]
    edition_type = next((value for value in properties if _EDITION_WORDS.search(value)), None)
    scoring = next((value for value in properties if value != edition_type), None)
    formats = [text for fragment in _WEIGHT.findall(header) if (text := _text(fragment))]
    page_format = next((value for value in formats if value.startswith("Pages")), None)
    pages = _PAGES.search(page_format) if page_format else None
    contributors = tuple(
        credit for row in _CONTRIBUTOR.findall(header) if (credit := _contributor(row)) is not None
    )
    return ParsedProduct(
        id=product_id,
        title=title,
        edition_number=_first(_SKU, header),
        subtitle=subtitle,
        composers=_composers(subtitle),
        contributors=contributors,
        edition_type=edition_type,
        scoring=scoring,
        format=page_format,
        page_count=int(pages.group(1)) if pages else None,
        ismn=_first(_ISMN, header),
        variants=tuple(dict.fromkeys(html.unescape(v).strip() for v in _VARIANT.findall(header))),
        description=_first(_DESCRIPTION, header),
        works=parse_contents(_region(page, _CONTENTS_START, _CONTENTS_END)),
    )
