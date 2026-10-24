"""Works with a catalogue number, from the Wikidata Query Service.

The population is every item with a composer (P86) and a *catalogue code*
(P528) qualified by the catalogue it belongs to (P972): BWV 1048, K. 550,
Hob. XVI:52. Those are the works whose identity Wikidata can vouch for, and
each brings its strong keys along:

- the catalogue codes, each with its catalogue's label and short name (P1813)
  -- a code is sometimes stated with its prefix ("K. 550") and sometimes bare
  ("550", catalogue "Köchel catalogue", short name "KV"), so the warehouse
  reads both (``composer_warehouse.works.sources``) rather than this adapter
  guessing a scheme;
- MusicBrainz work ids (P435) and ISWCs (P1827);
- the items it is *part of* (P361), which the warehouse turns into a
  ``part_of`` link to the parent work.

The item itself is the work's Wikidata id. This is a separate adapter from
``wikidata_works`` (works with a premiere) so each keeps its own population and
mirror: most catalogued works have no recorded premiere, and most premiered
works no catalogue code.

Paging is the composer population's (see the ``query`` module): one cheap
id-list query, then VALUES batches of detail rows, each batch checked for
coverage before it is mirrored. The multi-valued properties come back through
UNION branches, one value per row, so a work with many codes and parts costs
their sum rather than their product.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from composer_http import SourceSession

from .. import HttpSourceAdapter, RefreshCadence, WorkMentionDocument
from .parse import _BARE_QID, BASE_URL, _literal
from .query import LABEL_LANGUAGES, PAGE_SIZE, REQUEST_DELAY_S, TIMEOUT_S, _qid, _run_query, _values

log = logging.getLogger(__name__)

ENTITY = "http://www.wikidata.org/entity/"

CATALOGUE_ID_QUERY = """\
SELECT DISTINCT ?item WHERE { ?item wdt:P86 ?composer ; p:P528 ?code . ?code pq:P972 ?catalogue . }
"""

# The composer is required on every row, so each bound item returns at least
# one row through the catalogue-code branch it was selected by; the other
# branches add one row per value. The catalogue's short name is filtered to the
# label languages, since P1813 holds one value per language.
CATALOGUE_QUERY = """\
SELECT ?item ?itemLabel ?composer ?composerLabel ?code ?catalogue ?catalogueLabel ?catalogueShort
       ?musicbrainz ?iswc ?partOf
WHERE {{
  VALUES ?item {{ {values} }}
  ?item wdt:P86 ?composer .
  {{ ?item p:P528 ?statement . ?statement ps:P528 ?code .
     OPTIONAL {{ ?statement pq:P972 ?catalogue .
                 OPTIONAL {{ ?catalogue wdt:P1813 ?catalogueShort .
                             FILTER(LANG(?catalogueShort) IN ("en", "mul", "")) }} }} }}
  UNION {{ ?item wdt:P435 ?musicbrainz . }}
  UNION {{ ?item wdt:P1827 ?iswc . }}
  UNION {{ ?item wdt:P361 ?partOf . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{languages}" . }}
}}
"""


def _fetch_catalogue_qids(session: SourceSession) -> list[str]:
    """Every QID with a composer and a qualified catalogue code, numerically ordered."""
    qids = {_qid(row) for row in _run_query(session, CATALOGUE_ID_QUERY, "catalogued work id list")}
    return sorted(qids, key=lambda qid: (len(qid), qid))


def _fetch_catalogue_page(session: SourceSession, qids: list[str]) -> list[dict[str, Any]]:
    """The detail rows for one batch of works; a batch missing a bound id
    fails the run and is never mirrored (issue #181)."""
    query = CATALOGUE_QUERY.format(values=_values(qids), languages=LABEL_LANGUAGES)

    def covered(rows: list[dict[str, Any]]) -> bool:
        return not set(qids) - {_qid(row) for row in rows}

    rows = _run_query(session, query, f"page of {len(qids)} catalogued works", mirror=True, complete=covered)
    missing = set(qids) - {_qid(row) for row in rows}
    if missing:
        raise RuntimeError(
            f"wikidata page returned {len(qids) - len(missing)} of {len(qids)} requested works; "
            f"missing e.g. {sorted(missing)[:5]}"
        )
    return rows


def _label(row: dict[str, Any], var: str) -> str | None:
    """A label, unless the label service echoed a bare QID for want of one."""
    label = _literal(row, var)
    return label if label and not _BARE_QID.match(label) else None


def _entity_qid(row: dict[str, Any], var: str) -> str | None:
    value = _literal(row, var)
    return value.removeprefix(ENTITY) if value and value.startswith(ENTITY) else None


def _fold_catalogue_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group rows by work QID: label, composers (QID -> label), catalogue
    codes, MusicBrainz ids, ISWCs and the items it is part of."""
    works: dict[str, dict[str, Any]] = {}
    for row in rows:
        work = works.setdefault(
            _qid(row),
            {
                "label": _label(row, "itemLabel"),
                "composers": {},
                "codes": {},
                "mbids": set(),
                "iswcs": set(),
                "parts": set(),
            },
        )
        if (composer := _entity_qid(row, "composer")) and (name := _label(row, "composerLabel")):
            work["composers"][composer] = name
        if code := _literal(row, "code"):
            entry = work["codes"].setdefault(
                (code, _entity_qid(row, "catalogue")),
                {"code": code, "catalogue": _label(row, "catalogueLabel"), "catalogue_short": None},
            )
            short = _literal(row, "catalogueShort")
            if short and (entry["catalogue_short"] is None or short < entry["catalogue_short"]):
                entry["catalogue_short"] = short
        if mbid := _literal(row, "musicbrainz"):
            work["mbids"].add(mbid)
        if iswc := _literal(row, "iswc"):
            work["iswcs"].add(iswc)
        if part := _entity_qid(row, "partOf"):
            work["parts"].add(part)
    return works


def _mentions_from_rows(rows: list[dict[str, Any]], ingested_at: datetime) -> list[WorkMentionDocument]:
    """One work mention per (work, composer) with a nameable title and composer."""
    mentions: list[WorkMentionDocument] = []
    for qid, work in _fold_catalogue_rows(rows).items():
        title: str | None = work["label"]
        if not title:
            log.debug("skipping %s: no label", qid)
            continue
        codes = [
            {"catalogue_item": catalogue, **entry}
            for (_code, catalogue), entry in sorted(
                work["codes"].items(), key=lambda item: (item[0][0], item[0][1] or "")
            )
        ]
        raw = {
            "item": f"{ENTITY}{qid}",
            "catalogue_codes": codes,
            "musicbrainz_work": sorted(work["mbids"]),
            "iswc": sorted(work["iswcs"]),
            "part_of": [f"{ENTITY}{part}" for part in sorted(work["parts"], key=lambda q: (len(q), q))],
        }
        for composer_qid, composer in sorted(work["composers"].items()):
            mentions.append(
                WorkMentionDocument(
                    # keyed by composer too, as in ``wikidata_works``
                    id=f"{qid}:{composer_qid}",
                    url=f"{BASE_URL}/wiki/{qid}",
                    source_name=WikidataCatalogueAdapter.name,
                    ingested_at=ingested_at,
                    title=title,
                    composer=composer,
                    raw={**raw, "composer_item": f"{ENTITY}{composer_qid}"},
                )
            )
    return mentions


class WikidataCatalogueAdapter(HttpSourceAdapter[WorkMentionDocument]):
    name = "wikidata_catalogue"
    base_url = BASE_URL
    cadence = RefreshCadence.YEARLY
    request_delay_s = REQUEST_DELAY_S
    timeout_s = TIMEOUT_S

    def scrape(self, session: SourceSession, max_pages: int | None = None) -> Iterator[WorkMentionDocument]:
        """Yield a mention for every work Wikidata gives a catalogue number.
        ``max_pages`` caps the number of batches, for test runs."""
        qids = _fetch_catalogue_qids(session)
        if not qids:
            raise RuntimeError("wikidata returned no works with a catalogue code")
        batches = [qids[i : i + PAGE_SIZE] for i in range(0, len(qids), PAGE_SIZE)]
        if max_pages is not None and max_pages < len(batches):
            log.info("stopping after max_pages=%d", max_pages)
            batches = batches[:max_pages]
        log.info("wikidata_catalogue: %d works in %d pages", len(qids), len(batches))

        for page, batch in enumerate(batches, start=1):
            mentions = _mentions_from_rows(_fetch_catalogue_page(session, batch), datetime.now(UTC))
            log.info("wikidata_catalogue page %d/%d: %d mentions", page, len(batches), len(mentions))
            yield from mentions
