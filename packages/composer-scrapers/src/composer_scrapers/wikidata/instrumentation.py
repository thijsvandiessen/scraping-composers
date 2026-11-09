"""Works with a stated instrumentation (P870), from the Wikidata Query Service.

The population is every item with a composer (P86) and an *instrumentation*
(P870) statement that is not deprecated. Each statement names one instrument or
ensemble item ("flute", "orchestra", "string quartet"), and two qualifiers say
more about it:

- *quantity* (P1114): how many of it, "4" horns;
- *object has role* (P3831): the part it plays, "soloist" for a concerto's violin.

Each work yields what :mod:`~composer_scrapers.baerenreiter` yields for an
edition, the convention of :mod:`composer_schema.instrumentation`:

- a :class:`~composer_schema.WorkMentionDocument` per composer, so the warehouse
  matches it to the canonical work on its QID;
- an :class:`~composer_schema.EntityDocument` of kind ``work`` with
  ``written_for`` and ``includes_instrument`` edges;
- ``raw["instrumentation"]``: every statement as a part, its QID, label, count
  and roles kept, including the ones the category table does not know.

Each part's category is read from the instrument item's label through
:func:`~composer_schema.instrumentation.category_for`, the table the publisher
sources use, so Wikidata's "cello" and Bärenreiter's "Violoncello" are one
category. A label the table does not know gives no edge; the part keeps its QID
so the table can grow from the fetch log.

What the work is *for*, as against what it merely *includes*, is not stated, so
it is read from the shape of the list:

- an ensemble (:data:`~composer_schema.instrumentation.ENSEMBLES`) is always
  what the work is for, and its members are what it includes;
- an instrument whose role is a solo one is what the work is for;
- the other instruments are what the work is for only when the list is a short
  list of forces ("violin, piano"; "violin, orchestra"). A list with a count on
  it, an orchestral section ("string section") or more than
  :data:`MAX_FORCES` instruments is the parts an orchestra plays, so those
  instruments are only included: Brahms's Second is not a work for flute.

Paging is the other work adapters': one id-list query, then VALUES batches of
detail rows, each checked for coverage before it is mirrored.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from composer_http import SourceSession
from composer_schema.instrumentation import (
    ENSEMBLES,
    SECTIONS,
    category_for,
    members_of,
    parse_instrumentation,
)

from .. import EntityDocument, HttpSourceAdapter, RefreshCadence, SourceClaim, WorkMentionDocument
from .parse import _BARE_QID, BASE_URL, _language, _literal
from .query import LABEL_LANGUAGES, PAGE_SIZE, REQUEST_DELAY_S, TIMEOUT_S, _qid, _run_query, _values

log = logging.getLogger(__name__)

ENTITY = "http://www.wikidata.org/entity/"

#: The most instruments a list may name, ensembles and soloists aside, and
#: still be read as what the work is for. A piano quartet's four forces pass; a
#: symphony's dozen parts do not.
MAX_FORCES = 4

# Deprecated statements are left out of both queries alike, so a work whose only
# P870 is deprecated is in neither and the detail page's coverage check holds.
INSTRUMENTATION_ID_QUERY = """\
SELECT DISTINCT ?item WHERE {
  ?item wdt:P86 ?composer ; p:P870 ?statement .
  ?statement ps:P870 ?instrument ; wikibase:rank ?rank .
  FILTER(?rank != wikibase:DeprecatedRank)
}
"""

# One row per (composer, statement, quantity, role); a work has few of each
# but its statements, so the product stays small. The statement node is
# selected so two statements naming the same instrument ("violin" as soloist,
# "violin" in the orchestra) stay two parts.
INSTRUMENTATION_QUERY = """\
SELECT ?item ?itemLabel ?composer ?composerLabel ?statement ?instrument ?instrumentLabel
       ?quantity ?role ?roleLabel
WHERE {{
  VALUES ?item {{ {values} }}
  ?item wdt:P86 ?composer ; p:P870 ?statement .
  ?statement ps:P870 ?instrument ; wikibase:rank ?rank .
  FILTER(?rank != wikibase:DeprecatedRank)
  OPTIONAL {{ ?statement pq:P1114 ?quantity . }}
  OPTIONAL {{ ?statement pq:P3831 ?role . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{languages}" . }}
}}
"""


@dataclass
class Part:
    """One P870 statement: the item it names, and what was read from it."""

    item: str
    label: str | None
    count: int | None = None
    roles: set[str] = field(default_factory=set[str])

    @property
    def category(self) -> str | None:
        return category_for(self.label) if self.label else None

    @property
    def solo(self) -> bool:
        return any("solo" in role.casefold() for role in self.roles)

    def as_dict(self) -> dict[str, object]:
        """The part for ``raw``; empty fields left out."""
        out: dict[str, object] = {"item": self.item, "label": self.label, "instrument": self.category}
        if self.count is not None:
            out["count"] = self.count
        if self.roles:
            out["roles"] = sorted(self.roles)
        if self.solo:
            out["solo"] = True
        return out


@dataclass(frozen=True)
class Scoring:
    """What a work's parts say it is for and includes."""

    written_for: tuple[str, ...]
    includes: tuple[str, ...]


def _entity_qid(row: dict[str, Any], var: str) -> str | None:
    value = _literal(row, var)
    return value.removeprefix(ENTITY) if value and value.startswith(ENTITY) else None


def _label(row: dict[str, Any], var: str) -> str | None:
    """A label, unless the label service echoed a bare QID for want of one."""
    label = _literal(row, var)
    return label if label and not _BARE_QID.match(label) else None


def _count(value: str | None) -> int | None:
    """A positive whole quantity, or ``None``: "2", "+2" and "2.0" are 2."""
    if value is None:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return int(number) if number > 0 and number == number.to_integral_value() else None


def _expand(category: str) -> tuple[str, ...]:
    """*category* and the instruments it names ("piano four hands" -> piano)."""
    return parse_instrumentation(category) or (category,)


def scoring(parts: list[Part]) -> Scoring:
    """What *parts* say the work is for and includes; see the module docstring."""
    categories = [(part, category) for part in parts if (category := part.category) is not None]
    ensembles = [category for _, category in categories if category in ENSEMBLES]
    soloists = [category for part, category in categories if category not in ENSEMBLES and part.solo]
    others = [category for part, category in categories if category not in ENSEMBLES and not part.solo]
    parts_list = (
        any(part.count is not None for part, _ in categories)
        or any(category in SECTIONS for _, category in categories)
        or len(set(others)) > MAX_FORCES
    )
    forces = [*ensembles, *soloists, *([] if parts_list else others)]
    written_for = [name for category in forces for name in _expand(category)]
    instruments = [category for _, category in categories if category not in ENSEMBLES]
    includes = [*instruments, *members_of(ensembles)]
    return Scoring(tuple(dict.fromkeys(written_for)), tuple(dict.fromkeys(includes)))


@dataclass
class _Work:
    label: str | None
    language: str | None = None
    composers: dict[str, str] = field(default_factory=dict[str, str])
    parts: dict[str, Part] = field(default_factory=dict[str, Part])


def _fold_rows(rows: list[dict[str, Any]]) -> dict[str, _Work]:
    """Group rows by work QID: label, composers (QID -> label), and parts by statement."""
    works: dict[str, _Work] = {}
    for row in rows:
        work = works.setdefault(
            _qid(row), _Work(label=_label(row, "itemLabel"), language=_language(row, "itemLabel"))
        )
        if (composer := _entity_qid(row, "composer")) and (name := _label(row, "composerLabel")):
            work.composers[composer] = name
        # An "unknown value" instrument is no item, and says nothing to keep.
        if (statement := _literal(row, "statement")) is None or (
            instrument := _entity_qid(row, "instrument")
        ) is None:
            continue
        part = work.parts.setdefault(statement, Part(item=instrument, label=_label(row, "instrumentLabel")))
        if (count := _count(_literal(row, "quantity"))) is not None:
            part.count = count if part.count is None else max(part.count, count)
        if _entity_qid(row, "role") is not None and (role := _label(row, "roleLabel")):
            part.roles.add(role)
    return works


def _ordered(parts: dict[str, Part]) -> list[Part]:
    """Parts in a stable order: SPARQL gives the statements in none."""
    return [
        part
        for _, part in sorted(
            parts.items(), key=lambda kv: ((kv[1].label or "").casefold(), kv[1].item, kv[0])
        )
    ]


def _edge(predicate: str, kind: str, label: str, qid: str | None = None) -> SourceClaim:
    return SourceClaim(predicate, object_kind=kind, object_label=label, object_wikidata_id=qid)


def _documents(
    qid: str, work: _Work, ingested_at: datetime, tally: Counter[str]
) -> Iterator[WorkMentionDocument | EntityDocument]:
    """A mention per composer and one work entity, or nothing for an unnameable work."""
    if not work.label or not work.composers:
        tally["unnameable"] += 1
        return
    parts = _ordered(work.parts)
    if not parts:
        tally["no instrument item"] += 1
        return
    read = scoring(parts)
    unmatched = [part for part in parts if part.category is None]
    tally["works"] += 1
    tally["parts"] += len(parts)
    tally["parts unmatched"] += len(unmatched)
    composers = sorted(work.composers.items())
    url = f"{BASE_URL}/wiki/{qid}"
    raw: dict[str, Any] = {
        "item": f"{ENTITY}{qid}",
        "instrumentation": {
            "parts": [part.as_dict() for part in parts],
            "unmatched": [part.label or part.item for part in unmatched],
            "written_for": list(read.written_for),
            "includes": list(read.includes),
        },
    }
    for composer_qid, composer in composers:
        yield WorkMentionDocument(
            # keyed by composer too, as in ``wikidata_works``
            id=f"{qid}:{composer_qid}",
            url=url,
            source_name=WikidataInstrumentationAdapter.name,
            ingested_at=ingested_at,
            title=work.label,
            composer=composer,
            language=work.language,
            raw={**raw, "composer_item": f"{ENTITY}{composer_qid}"},
        )
    claims = [_edge("composed_by", "person", name, composer_qid) for composer_qid, name in composers]
    claims += [_edge("written_for", "instrumentation", name) for name in read.written_for]
    claims += [_edge("includes_instrument", "instrumentation", name) for name in read.includes]
    yield EntityDocument(
        id=qid,
        url=url,
        source_name=WikidataInstrumentationAdapter.name,
        ingested_at=ingested_at,
        # composer-qualified, as the publisher sources' work entities are
        name=f"{work.label} ({' / '.join(name for _, name in composers)})",
        kind="work",
        language=work.language,
        raw=raw,
        claims=tuple(claims),
    )


def documents_from_rows(
    rows: list[dict[str, Any]], ingested_at: datetime, tally: Counter[str] | None = None
) -> list[WorkMentionDocument | EntityDocument]:
    """Every document one page of detail rows yields."""
    tally = Counter[str]() if tally is None else tally
    return [
        document
        for qid, work in _fold_rows(rows).items()
        for document in _documents(qid, work, ingested_at, tally)
    ]


def _fetch_instrumentation_qids(session: SourceSession) -> list[str]:
    """Every QID with a composer and a P870 statement, numerically ordered."""
    rows = _run_query(session, INSTRUMENTATION_ID_QUERY, "instrumentation work id list")
    return sorted({_qid(row) for row in rows}, key=lambda qid: (len(qid), qid))


def _fetch_instrumentation_page(session: SourceSession, qids: list[str]) -> list[dict[str, Any]]:
    """The detail rows for one batch of works; a batch missing a bound id
    fails the run and is never mirrored (issue #181)."""
    query = INSTRUMENTATION_QUERY.format(values=_values(qids), languages=LABEL_LANGUAGES)

    def covered(rows: list[dict[str, Any]]) -> bool:
        return not set(qids) - {_qid(row) for row in rows}

    rows = _run_query(
        session, query, f"page of {len(qids)} instrumented works", mirror=True, complete=covered
    )
    missing = set(qids) - {_qid(row) for row in rows}
    if missing:
        raise RuntimeError(
            f"wikidata page returned {len(qids) - len(missing)} of {len(qids)} requested works; "
            f"missing e.g. {sorted(missing)[:5]}"
        )
    return rows


class WikidataInstrumentationAdapter(HttpSourceAdapter[WorkMentionDocument | EntityDocument]):
    name = "wikidata_instrumentation"
    base_url = BASE_URL
    cadence = RefreshCadence.YEARLY
    request_delay_s = REQUEST_DELAY_S
    timeout_s = TIMEOUT_S

    def scrape(
        self, session: SourceSession, max_pages: int | None = None
    ) -> Iterator[WorkMentionDocument | EntityDocument]:
        """Yield the documents of every work Wikidata states an instrumentation
        for. ``max_pages`` caps the number of batches, for test runs."""
        qids = _fetch_instrumentation_qids(session)
        if not qids:
            raise RuntimeError("wikidata returned no works with an instrumentation")
        batches = [qids[i : i + PAGE_SIZE] for i in range(0, len(qids), PAGE_SIZE)]
        if max_pages is not None and max_pages < len(batches):
            log.info("stopping after max_pages=%d", max_pages)
            batches = batches[:max_pages]
        log.info("wikidata_instrumentation: %d works in %d pages", len(qids), len(batches))

        tally: Counter[str] = Counter()
        unmatched: Counter[str] = Counter()
        for page, batch in enumerate(batches, start=1):
            documents = documents_from_rows(
                _fetch_instrumentation_page(session, batch), datetime.now(UTC), tally
            )
            for document in documents:
                if isinstance(document, EntityDocument):
                    unmatched.update(document.raw["instrumentation"]["unmatched"])
            log.info("wikidata_instrumentation page %d/%d: %d documents", page, len(batches), len(documents))
            yield from documents
        log.info("wikidata_instrumentation: %s", ", ".join(f"{n} {what}" for what, n in tally.items()))
        if unmatched:
            log.info(
                "wikidata_instrumentation: most common unmatched instruments: %s",
                ", ".join(f"{label} ({n})" for label, n in unmatched.most_common(20)),
            )
