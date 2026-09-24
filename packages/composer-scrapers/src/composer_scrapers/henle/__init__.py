"""G. Henle Verlag's catalogue (henle.de): every Urtext edition it sells.

A publisher's catalogue, read like :mod:`~composer_scrapers.baerenreiter`: each
work yields a :class:`~composer_schema.WorkMentionDocument` for the work matcher
and an :class:`~composer_schema.EntityDocument` of kind ``work`` whose claims make
its metadata queryable, labelled composer-qualified because entity dedup keys on
the label alone.

What this source adds is Henle's **level of difficulty**: every piano, violin,
flute, cello and clarinet work in the catalogue is graded from 1 (easy) to 9
(difficult), and graded *per work*, in the edition's contents table — so a volume
of nine Mozart sonatas says which of them is the hardest. That is why a volume is
read row by row:

- **several contents rows** — each row is a work of its own (id ``HN-1#3``) with
  its composer, scoring and ``difficulty_level``, and a ``part_of`` edge to the
  volume. The volume is only an entity (id ``HN-1``), carrying the facts about
  the printed edition — editor, fingering, ISMN, pages; the matcher is not asked
  to find a work called "Piano Sonatas, Volume I".
- **one row or none** — the edition *is* the work (id ``HN-659``): one mention and
  one entity carrying every claim. The work's title is the row's ("Waltz a minor
  op. 34,2") when there is one, the edition's otherwise.

The parts, the study score and the complete-edition volume of one work are
separate products (HN 741, HN 9741, …), so they arrive as separate mentions of
the same composer-qualified title; the matcher and entity dedup fold them.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime

from composer_http import open_page_cache
from composer_schema.instrumentation import category_for, members_of, parse_instrumentation

from .. import EntityDocument, RefreshCadence, SourceAdapter, SourceClaim, WorkMentionDocument
from .fetch import BASE_URL, fetch_sitemap, iter_products, make_client, product_urls
from .products import Contributor, ParsedProduct, parse_product

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "PUBLISHER", "HenleAdapter", "written_for"]

PUBLISHER = "G. Henle Verlag"

#: "Violin Concertos", "Piano Concerto": the solo instrument with orchestra. The
#: one scoring shape Henle uses that the shared tables do not read, since it
#: names a genre rather than the forces.
_CONCERTO = re.compile(r"^(?P<solo>.+?)\s+concertos?$", re.IGNORECASE)

#: Contributor roles that are claims; the rest ("Preface", "Piano reduction",
#: "Cadenzas", …) stay in ``raw`` only.
_EDITOR_ROLES = frozenset({"editor"})
_FINGERING_ROLE = "fingering"


def written_for(scoring: str | None) -> tuple[str, ...]:
    """The scoring categories Henle's scoring names, or ``()`` when it names none
    the tables know ("Chamber music with winds" is a shelf, not a scoring)."""
    if not scoring:
        return ()
    if found := parse_instrumentation(scoring):
        return found
    if (concerto := _CONCERTO.match(scoring.strip())) and (solo := category_for(concerto["solo"])):
        return (solo, "orchestra")
    return ()


def _label(title: str, composers: tuple[str, ...]) -> str:
    """Composer-qualified work label; see the module docstring for why."""
    return f"{title} ({' / '.join(composers)})" if composers else title


def _edge(predicate: str, kind: str, label: str) -> SourceClaim:
    return SourceClaim(predicate, object_kind=kind, object_label=label)


def _work_claims(composers: tuple[str, ...], scoring: str | None, grade: int | None) -> list[SourceClaim]:
    """What is true of the work whichever edition prints it."""
    categories = written_for(scoring)
    claims = [_edge("composed_by", "person", name) for name in composers]
    claims += [_edge("written_for", "instrumentation", name) for name in categories]
    claims += [_edge("includes_instrument", "instrumentation", name) for name in members_of(categories)]
    if scoring:
        claims.append(SourceClaim("has_scoring", value=scoring))
    if grade is not None:
        claims.append(SourceClaim("difficulty_level", value=str(grade)))
    return claims


def _contributor_claims(contributor: Contributor) -> Iterator[SourceClaim]:
    roles = [role.casefold() for role in contributor.roles]
    if any(role in _EDITOR_ROLES for role in roles):
        yield _edge("edited_by", "person", contributor.name)
    if any(role.startswith(_FINGERING_ROLE) for role in roles):
        yield _edge("fingering_by", "person", contributor.name)


def _edition_claims(product: ParsedProduct) -> list[SourceClaim]:
    """What is true of this printed edition: a claim on the work, by the
    convention of :mod:`composer_extract.facts`, so two editions merge."""
    claims = [_edge("published_by", "publisher", PUBLISHER)]
    claims += [claim for person in product.contributors for claim in _contributor_claims(person)]
    literals: list[tuple[str, object]] = [
        ("edition_type", product.edition_type),
        ("catalogue_number", product.edition_number),
        ("ismn", product.ismn),
        ("page_count", product.page_count),
    ]
    claims += [SourceClaim(predicate, value=str(value)) for predicate, value in literals if value]
    return claims


def _raw(product: ParsedProduct, url: str) -> dict[str, object]:
    """Every field the page states, so a later pass can use what this adapter
    does not yet claim without refetching."""
    return {
        "url": url,
        "edition_number": product.edition_number,
        "title": product.title,
        "subtitle": product.subtitle,
        "composers": list(product.composers),
        "contributors": [person.as_dict() for person in product.contributors],
        "edition_type": product.edition_type,
        "scoring": product.scoring,
        "written_for": list(written_for(product.scoring)),
        "format": product.format,
        "page_count": product.page_count,
        "ismn": product.ismn,
        "variants": list(product.variants),
        "description": product.description,
        "contents": [item.as_dict() for item in product.works],
    }


class HenleAdapter(SourceAdapter):
    name = "henle"
    base_url = BASE_URL
    # A publisher's back catalogue changes slowly: new editions appear, existing
    # ones rarely move.
    cadence = RefreshCadence.YEARLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        """Walk the catalogue, yielding a work mention and a work entity per work.

        ``max_pages`` caps the number of product pages requested.
        """
        ingested_at = datetime.now(UTC)
        cache = open_page_cache()
        tally: Counter[str] = Counter()
        with make_client() as client:
            products = product_urls(fetch_sitemap(client))
            tally["listed"] = len(products)
            for product_id, url, page in iter_products(client, products, cache, max_pages):
                product = parse_product(product_id, page)
                if product is None:
                    tally["unreadable"] += 1
                    continue
                if not product.is_music:
                    tally["not sheet music"] += 1
                    continue
                tally["editions"] += 1
                if product.scoring and not written_for(product.scoring):
                    tally["scorings unrecognised"] += 1
                yield from self._documents(product, url, ingested_at, tally)
        if cache is not None:
            log.info("henle: page cache %s", cache.summary())
        log.info("henle: %s", ", ".join(f"{count} {what}" for what, count in tally.items()))

    def _documents(
        self, product: ParsedProduct, url: str, ingested_at: datetime, tally: Counter[str]
    ) -> Iterator[EntityDocument | WorkMentionDocument]:
        raw = _raw(product, url)
        if len(product.works) > 1:
            tally["volumes"] += 1
            yield from self._volume(product, url, raw, ingested_at, tally)
            return
        item = product.works[0] if product.works else None
        title = item.title if item is not None else product.title
        composers = (item.composer,) if item is not None and item.composer else product.composers
        grade = item.grade if item is not None else None
        if grade is not None:
            tally["graded works"] += 1
        yield WorkMentionDocument(
            id=product.id,
            url=url,
            source_name=self.name,
            ingested_at=ingested_at,
            title=title,
            composer=composers[0] if composers else None,
            raw=raw,
        )
        yield EntityDocument(
            id=product.id,
            url=url,
            source_name=self.name,
            ingested_at=ingested_at,
            name=_label(title, composers),
            kind="work",
            raw=raw,
            claims=(*_work_claims(composers, product.scoring, grade), *_edition_claims(product)),
        )

    def _volume(
        self,
        product: ParsedProduct,
        url: str,
        raw: dict[str, object],
        ingested_at: datetime,
        tally: Counter[str],
    ) -> Iterator[EntityDocument | WorkMentionDocument]:
        """One work per contents row, and the volume as an entity of its own."""
        volume = _label(product.title, product.composers)
        yield EntityDocument(
            id=product.id,
            url=url,
            source_name=self.name,
            ingested_at=ingested_at,
            name=volume,
            kind="work",
            raw=raw,
            claims=(*_work_claims(product.composers, product.scoring, None), *_edition_claims(product)),
        )
        for item in product.works:
            composers = (item.composer,) if item.composer else product.composers
            if not composers:
                tally["rows without a composer"] += 1
                continue
            tally["volume works"] += 1
            if item.grade is not None:
                tally["graded works"] += 1
            row_id = f"{product.id}#{item.index}"
            row_raw: dict[str, object] = {
                "url": url,
                "volume_id": product.id,
                "volume_title": product.title,
                "edition_number": product.edition_number,
                "scoring": product.scoring,
                **item.as_dict(),
            }
            yield WorkMentionDocument(
                id=row_id,
                url=url,
                source_name=self.name,
                ingested_at=ingested_at,
                title=item.title,
                composer=composers[0],
                raw=row_raw,
            )
            yield EntityDocument(
                id=row_id,
                url=url,
                source_name=self.name,
                ingested_at=ingested_at,
                name=_label(item.title, composers),
                kind="work",
                raw=row_raw,
                claims=(
                    *_work_claims(composers, product.scoring, item.grade),
                    _edge("part_of", "work", volume),
                ),
            )
