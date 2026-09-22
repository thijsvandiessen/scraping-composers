"""Bärenreiter's catalogue (baerenreiter.com): every edition it sells or hires.

A publisher's catalogue, like :mod:`~composer_scrapers.boosey`, and read the same
way: each product yields a :class:`~composer_schema.WorkMentionDocument` for the
work matcher and an :class:`~composer_schema.EntityDocument` of kind ``work``
whose claims make its metadata queryable. The entity label is composer-qualified
for the reason boosey's is — entity dedup keys on the label alone.

What this source adds over boosey is instrumentation *in detail*: a part-by-part
list with counts and doublings ("Flute (2) (Piccolo flute), Horn (4), …"), read
by :mod:`.instrumentation`. It lands three ways, the convention of
:mod:`composer_extract.scoring`:

- ``written_for`` edges: what the work is *for*, from the "Scoring" field;
- ``includes_instrument`` edges: every instrument in the detail list;
- ``raw["instrumentation"]``: the parts themselves, counts and doublings
  included — structure, not claims, as nobody compares "4" across sources.

A product is an *edition*, not a work, so several products share a work (score,
parts, vocal score, hire material); the work matcher folds those together. Two
kinds are left out on purpose:

- a digital product whose printed twin is in the catalogue (``BA05163D`` beside
  ``BA05163``): the same edition as a PDF, so fetching it would only double the
  work's mentions. Its id is recorded on the print product instead.
- books, magazines and media (:data:`.products.NON_MUSIC_TYPES`): their contents
  are chapters, not works.

An anthology's contents are works in their own right; each item with a composer
becomes a work mention of its own (id ``<product>#<n>``), while the anthology
itself is only an entity — it is not a work the matcher should look for.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from composer_http import open_page_cache
from composer_schema.shorthand import parse_shorthand

from .. import EntityDocument, RefreshCadence, SourceAdapter, SourceClaim, WorkMentionDocument
from .fetch import BASE_URL, fetch_sitemap, iter_products, make_client, product_ids, product_url
from .instrumentation import (
    Detail,
    ensemble_members,
    parse_detail,
    scoring_categories,
    string_instruments,
    string_section,
)
from .products import ParsedProduct, parse_product, twin_print_id

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "BaerenreiterAdapter"]


def _entity_label(product: ParsedProduct) -> str:
    """Composer-qualified work label; see the module docstring for why."""
    if product.composers:
        return f"{product.title} ({' / '.join(product.composers)})"
    return product.title


def _instrumentation(product: ParsedProduct) -> tuple[Detail, tuple[str, ...], tuple[str, ...]]:
    """The detail list, what the work is for, and every instrument it includes."""
    detail = parse_detail(product.instrumentation_detail)
    written_for = scoring_categories(product.scoring or product.edition_scoring)
    included = [*detail.instruments, *ensemble_members(written_for)]
    # The shorthand is the only instrumentation a sixth of the catalogue states;
    # where the detail list exists it says the same thing more plainly.
    if not detail.parts and (stated := product.instrumentation_shorthand):
        if (shorthand := parse_shorthand(stated)) is not None:
            included.extend(shorthand.instruments)
        if (strings := string_section(stated)) is not None:
            included.extend(string_instruments(strings))
    return detail, written_for, tuple(dict.fromkeys(included))


def _edge(predicate: str, kind: str, label: str) -> SourceClaim:
    return SourceClaim(predicate, object_kind=kind, object_label=label)


def _claims(
    product: ParsedProduct, written_for: tuple[str, ...], included: tuple[str, ...]
) -> list[SourceClaim]:
    """Typed assertions about the work and this edition of it. Values stay
    verbatim except duration (whole minutes) and page count, so they compare
    across sources."""
    claims = [_edge("composed_by", "person", name) for name in product.composers]
    claims += [_edge("written_for", "instrumentation", name) for name in written_for]
    claims += [_edge("includes_instrument", "instrumentation", name) for name in included]
    claims += [_edge("text_by", "person", name) for name in product.librettists]
    claims += [_edge("arranged_by", "person", name) for name in product.arrangers]
    if product.publisher:
        claims.append(_edge("published_by", "publisher", product.publisher))
    literals: list[tuple[str, object]] = [
        ("has_scoring", product.scoring),
        ("composed_in", product.composed_in),
        ("has_duration", product.duration_minutes),
        ("catalogue_number", product.edition_number),
        ("ismn", product.ismn),
        ("isbn", product.isbn),
        ("page_count", product.page_count),
        *(("edition_type", kind) for kind in product.product_types),
    ]
    claims += [SourceClaim(predicate, value=str(value)) for predicate, value in literals if value]
    return claims


def _raw(
    product: ParsedProduct,
    payload: dict[str, Any],
    detail: Detail,
    digital_twin: str | None,
) -> dict[str, object]:
    """The API record verbatim, plus the fields as the product page labels them,
    so a later pass can structure what this adapter does not yet claim."""
    stated_shorthand = product.instrumentation_shorthand
    shorthand = parse_shorthand(stated_shorthand) if stated_shorthand else None
    return {
        "api": payload,
        "url": product_url(product.id),
        "edition_number": product.edition_number,
        "title": product.title,
        "subtitle": product.subtitle,
        "composers": list(product.composers),
        "librettists": list(product.librettists),
        "arrangers": list(product.arrangers),
        "foreword_by": product.foreword_by,
        "scoring": product.scoring,
        "instrumentation": {
            "detail": product.instrumentation_detail,
            "parts": [part.as_dict() for part in detail.parts],
            "unmatched": list(detail.unmatched),
            "shorthand": product.instrumentation_shorthand,
            "shorthand_parsed": shorthand.as_dict() if shorthand is not None else None,
            "string_section": string_section(stated_shorthand),
            "edition_scoring": product.edition_scoring,
        },
        "composed_in": product.composed_in,
        "duration": product.duration,
        "duration_minutes": product.duration_minutes,
        "product_format": list(product.product_types),
        "remark": product.remark,
        "publisher": product.publisher,
        "manufacturer": product.manufacturer,
        "availability": product.availability,
        "state": product.state,
        "fixed_retail_price": product.fixed_retail_price,
        "pages": product.pages,
        "binding": product.binding,
        "searchable": product.searchable,
        "digital": product.digital,
        "digital_edition_id": digital_twin,
    }


def _item_mentions(
    product: ParsedProduct, url: str, source_name: str, ingested_at: datetime
) -> Iterator[WorkMentionDocument]:
    """One work mention per anthology item with a composer: the item's own credit,
    or the anthology's when it names exactly one composer."""
    fallback = product.composers[0] if len(product.composers) == 1 else None
    for index, item in enumerate(product.items):
        title = item.get("titTiMain") or item.get("title")
        composer = item.get("titAuCompos") or fallback
        if not isinstance(title, str) or not title.strip() or not isinstance(composer, str):
            continue
        scoring = item.get("titBstz")
        detail = parse_detail(scoring if isinstance(scoring, str) else None)
        yield WorkMentionDocument(
            id=f"{product.id}#{index}",
            url=url,
            source_name=source_name,
            ingested_at=ingested_at,
            title=title.strip(),
            composer=composer,
            raw={
                "anthology_id": product.id,
                "anthology_title": product.title,
                "item": item,
                "instrumentation": {"parts": [part.as_dict() for part in detail.parts]},
            },
        )


def skipped_digital_twins(ids: list[str]) -> set[str]:
    """The digital products whose printed twin the catalogue also lists."""
    catalogue = set(ids)
    return {pid for pid in ids if (twin := twin_print_id(pid)) is not None and twin in catalogue}


class BaerenreiterAdapter(SourceAdapter):
    name = "baerenreiter"
    base_url = BASE_URL
    # A publisher's back catalogue changes slowly: new editions appear, existing
    # ones rarely move.
    cadence = RefreshCadence.YEARLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        """Walk the catalogue, yielding a work mention and a work entity per edition.

        ``max_pages`` caps the number of product records requested.
        """
        ingested_at = datetime.now(UTC)
        cache = open_page_cache()
        tally: Counter[str] = Counter()
        with make_client() as client:
            ids = product_ids(fetch_sitemap(client))
            skipped = skipped_digital_twins(ids)
            digital_of = {twin_print_id(pid): pid for pid in skipped}
            tally["listed"], tally["digital twins skipped"] = len(ids), len(skipped)
            wanted = [pid for pid in ids if pid not in skipped]
            for product_id, payload in iter_products(client, wanted, cache, max_pages):
                product = parse_product(payload)
                if product is None:
                    tally["unreadable"] += 1
                    continue
                if not product.is_music:
                    tally["not sheet music"] += 1
                    continue
                tally["editions"] += 1
                yield from self._documents(product, payload, digital_of.get(product_id), ingested_at, tally)
        log.info("baerenreiter: %s", ", ".join(f"{count} {what}" for what, count in tally.items()))

    def _documents(
        self,
        product: ParsedProduct,
        payload: dict[str, Any],
        digital_twin: str | None,
        ingested_at: datetime,
        tally: Counter[str],
    ) -> Iterator[EntityDocument | WorkMentionDocument]:
        url = product_url(product.id)
        detail, written_for, included = _instrumentation(product)
        tally["instrument parts"] += len(detail.parts)
        tally["instrument parts unmatched"] += len(detail.unmatched)
        raw = _raw(product, payload, detail, digital_twin)
        if product.is_anthology:
            for mention in _item_mentions(product, url, self.name, ingested_at):
                tally["anthology items"] += 1
                yield mention
        else:
            yield WorkMentionDocument(
                id=product.id,
                url=url,
                source_name=self.name,
                ingested_at=ingested_at,
                title=product.title,
                composer=product.composers[0] if product.composers else None,
                raw=raw,
            )
        yield EntityDocument(
            id=product.id,
            url=url,
            source_name=self.name,
            ingested_at=ingested_at,
            name=_entity_label(product),
            kind="work",
            raw=raw,
            claims=tuple(_claims(product, written_for, included)),
        )
