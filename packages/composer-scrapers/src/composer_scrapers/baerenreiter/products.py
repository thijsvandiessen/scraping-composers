"""One product record of the Bärenreiter shop API, read into named fields.

The API speaks the publisher's internal field names, German abbreviations with an
``art`` (*Artikel*, a shop product) prefix: ``artAuCompos`` is the composer,
``artTiMain`` the main title, and the ``artBstz…`` (*Besetzung*, scoring) family
the instrumentation. :class:`ParsedProduct` gives them the names the site itself
shows (taken from its English i18n bundle), and this module is the one place that
knows the mapping.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any

#: A credit joins several people with " / " and may end "et al.".
_COMPOSER_SEPARATOR = " / "
_ET_AL = re.compile(r"\s+et al\.?$")
#: ``artPagForm``: "XV, 45 S. - 24,0 x 30,5 cm". Only a single count is a page
#: count; "IV, 32/10/10 S." is a score and its parts, which no one number states.
_PAGES = re.compile(r"(?:[IVXLC]+,\s*)?(\d+)\s*S\.")
_DURATION = re.compile(r"(\d+):(\d{2}):(\d{2})")
_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
#: "Performance score (3)": how many of that item the product holds.
_TYPE_COUNT = re.compile(r"\s*\(\d+\)$")

#: Product types that are not sheet music. Their ``items`` are book chapters or
#: magazine articles, not works, so they are never read as works.
NON_MUSIC_TYPES = frozenset(
    {
        "Book",
        "Magazine",
        "CD",
        "USB flash drive",
        "Accessories",
        "Catalogue of works",
        "Critical commentary",
        "Libretto / Text",
    }
)

#: ``state`` as the site's availability light (``BVDeliveryLight``) shows it.
#: 91–94 display a server-side message that the product API does not carry, so
#: they are left unnamed rather than guessed; the code is kept in ``raw``.
AVAILABILITY: dict[int, str] = {
    0: "Available",
    3: "Hire material",
    34: "Hire material",
    33: "On request",
    4: "In preparation",
    5: "Temporarily out of stock",
    6: "Temporarily out of stock",
    8: "Temporarily out of stock",
    7: "Out of print",
    9: "Out of print",
    # Not in the light's switch, but exactly the digital products carry it.
    96: "Digital edition",
    97: "Free digital edition",
}


@dataclass(frozen=True)
class ParsedProduct:
    """A shop product, under the labels the product page gives its fields."""

    id: str
    title: str
    #: "Edition number": the order number as printed, "BA 11625-72".
    edition_number: str | None = None
    subtitle: str | None = None
    composers: tuple[str, ...] = ()
    #: "Librettist" — like the composer credit, possibly several people.
    librettists: tuple[str, ...] = ()
    arrangers: tuple[str, ...] = ()
    #: "Foreword / Introduction" — not the editor, whom the API does not name.
    foreword_by: str | None = None
    #: "Scoring": the work's forces as categories ("Soprano solo, Strings").
    scoring: str | None = None
    #: "Instrumentation in detail": one entry per part ("Horn (4), …").
    instrumentation_detail: str | None = None
    #: "Instrumentation": orchestral shorthand ("str (4.4.3.2.1)").
    instrumentation_shorthand: str | None = None
    #: "Scoring" of this edition, where it differs from the work's.
    edition_scoring: str | None = None
    #: "Date of composition", verbatim ("2025", "1723/24").
    composed_in: str | None = None
    #: "Duration (approx.)", verbatim ("00:11:00").
    duration: str | None = None
    #: "Product format", split: ("Score", "Urtext edition").
    product_types: tuple[str, ...] = ()
    remark: str | None = None
    publisher: str | None = None
    #: "Manufacturer Identification (GPSR)", as plain text.
    manufacturer: str | None = None
    state: int | None = None
    fixed_retail_price: bool | None = None
    ismn: str | None = None
    isbn: str | None = None
    #: "Pages / format", verbatim ("XV, 45 S. - 24,0 x 30,5 cm").
    pages: str | None = None
    binding: str | None = None
    digital: bool = False
    #: Whether the shop's search lists the product at all.
    searchable: bool = True
    #: The contents of an anthology (or chapters of a book), as the API gives them.
    items: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def availability(self) -> str | None:
        return AVAILABILITY.get(self.state) if self.state is not None else None

    @property
    def duration_minutes(self) -> int | None:
        return duration_minutes(self.duration)

    @property
    def page_count(self) -> int | None:
        return page_count(self.pages)

    @property
    def is_music(self) -> bool:
        """False for books, magazines and media — products that are not a score."""
        return not NON_MUSIC_TYPES.intersection(self.product_types)

    @property
    def is_anthology(self) -> bool:
        """A collection of works, whose ``items`` are the works themselves.

        Only then are items works: a single work's items are its movements, and
        a book's are its chapters.
        """
        return "Anthology" in self.product_types


def _text(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str):
        return None
    return " ".join(html.unescape(value).split()) or None


def people(credit: str | None) -> tuple[str, ...]:
    """The people a credit names, verbatim ("Beethoven, Ludwig van")."""
    if not credit:
        return ()
    names = (_ET_AL.sub("", name).strip() for name in credit.split(_COMPOSER_SEPARATOR))
    return tuple(name for name in names if name)


def duration_minutes(duration: str | None) -> int | None:
    """``"00:11:00"`` -> 11, rounded to the nearest minute; ``None`` if unreadable."""
    if not duration or not (found := _DURATION.fullmatch(duration.strip())):
        return None
    hours, minutes, seconds = (int(group) for group in found.groups())
    total = hours * 60 + minutes + round(seconds / 60)
    return total or None


def page_count(pages: str | None) -> int | None:
    if not pages or not (found := _PAGES.match(pages.strip())):
        return None
    return int(found.group(1))


def manufacturer_text(raw: str | None) -> str | None:
    """The GPSR block, which the API sends as entity-encoded HTML, as plain lines."""
    if not raw:
        return None
    text = html.unescape(_TAG.sub("", _BREAK.sub("\n", raw)))
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line) or None


def product_types(display: str | None) -> tuple[str, ...]:
    """``"Performance score (3), Urtext edition"`` -> ("Performance score", "Urtext edition")."""
    if not display:
        return ()
    return tuple(_TYPE_COUNT.sub("", part).strip() for part in display.split(",") if part.strip())


def twin_print_id(product_id: str) -> str | None:
    """The printed edition a digital product's id names: "BA05163D" -> "BA05163".

    Every digital product's id is its print twin's with a ``D`` appended; whether
    that print product exists is for the caller to check against the catalogue.
    """
    return product_id[:-1] if product_id.endswith("D") and len(product_id) > 1 else None


def parse_product(payload: dict[str, Any]) -> ParsedProduct | None:
    """The product in *payload*, or ``None`` for a record without id or title."""
    product_id = payload.get("id")
    title = _text(payload, "title") or _text(payload, "artTiMain")
    if not isinstance(product_id, str) or not title:
        return None
    state = payload.get("state")
    fixed_price = payload.get("priFixRetP")
    items = payload.get("items")
    return ParsedProduct(
        id=product_id,
        title=title,
        edition_number=_text(payload, "orderId"),
        subtitle=_text(payload, "artTiSub"),
        composers=people(_text(payload, "artAuCompos")),
        librettists=people(_text(payload, "artAuText")),
        arrangers=people(_text(payload, "artAuArr")),
        foreword_by=_text(payload, "artAuPreamble"),
        scoring=_text(payload, "artBstzMainDispl"),
        instrumentation_detail=_text(payload, "artBstzDispl"),
        instrumentation_shorthand=_text(payload, "artBstzOrchNum"),
        edition_scoring=_text(payload, "artInstrDispl"),
        composed_in=_text(payload, "artTiDatOr"),
        duration=_text(payload, "artLength"),
        product_types=product_types(_text(payload, "artPrTyDispl")),
        remark=_text(payload, "artRemarks"),
        publisher=_text(payload, "artAdmin"),
        manufacturer=manufacturer_text(
            gpsr if isinstance(gpsr := payload.get("artGPSRManuf"), str) else None
        ),
        state=state if isinstance(state, int) else None,
        fixed_retail_price=fixed_price if isinstance(fixed_price, bool) else None,
        ismn=_text(payload, "artISMN"),
        isbn=_text(payload, "artISBN"),
        pages=_text(payload, "artPagForm"),
        binding=_text(payload, "artAppearDispl"),
        digital=payload.get("digital") is True,
        searchable=payload.get("artAnzeigeSuche") is not False,
        items=tuple(item for item in items if isinstance(item, dict)) if isinstance(items, list) else (),
    )
