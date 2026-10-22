"""Works with a recorded premiere, from the Wikidata Query Service.

The population is every item with a *date of first performance* (P1191) and a
composer (P86) -- the works whose premiere Wikidata knows. It is paged exactly
like the composer population (see the ``query`` module): one cheap id-list
query, then VALUES batches of detail rows, each batch checked for coverage.

Each work yields one work mention per composer, carrying the premiere under the
``raw["premiere"]`` convention (see :mod:`composer_schema.premiere`): the
earliest P1191 date at its recorded precision, and the *location of first
performance* (P4647) labels. P1191 is the first performance of the work itself,
so it is a world premiere and belongs on the work.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from composer_http import SourceSession
from composer_schema.premiere import PREMIERE_KEY, supersedes

from .. import HttpSourceAdapter, RefreshCadence, WorkMentionDocument
from .parse import _BARE_QID, BASE_URL, _format_time, _literal
from .query import LABEL_LANGUAGES, PAGE_SIZE, REQUEST_DELAY_S, TIMEOUT_S, _qid, _run_query, _values

log = logging.getLogger(__name__)

WORK_ID_QUERY = """\
SELECT DISTINCT ?item WHERE { ?item wdt:P1191 ?premiere ; wdt:P86 ?composer . }
"""

# Truthy premiere date joined to its value node for the precision, as for birth
# dates in QUERY. The precision join is OPTIONAL so an "unknown value" date (a
# blank node with no timeValue) still returns its row and the coverage check
# holds; ``_literal`` then drops the blank node. Several composers, dates or
# places multiply rows, but a work has few of each.
WORK_QUERY = """\
SELECT ?item ?itemLabel ?composer ?composerLabel ?premiere ?premierePrecision ?premierePlaceLabel
WHERE {{
  VALUES ?item {{ {values} }}
  ?item wdt:P86 ?composer ;
        wdt:P1191 ?premiere .
  OPTIONAL {{ ?item p:P1191/psv:P1191
                [ wikibase:timeValue ?premiere ; wikibase:timePrecision ?premierePrecision ] . }}
  OPTIONAL {{ ?item wdt:P4647 ?premierePlace . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{languages}" . }}
}}
"""


def _fetch_work_qids(session: SourceSession) -> list[str]:
    """Every QID with a premiere date and a composer, numerically ordered."""
    qids = {_qid(row) for row in _run_query(session, WORK_ID_QUERY, "work id list")}
    return sorted(qids, key=lambda qid: (len(qid), qid))


def _fetch_work_page(session: SourceSession, qids: list[str]) -> list[dict[str, Any]]:
    """The detail rows for one batch of works. Every bound id matched the id
    query's required patterns, so each must come back; a short answer means
    rows went missing in transit (issue #181) and fails the run -- and is
    never mirrored."""
    query = WORK_QUERY.format(values=_values(qids), languages=LABEL_LANGUAGES)

    def covered(rows: list[dict[str, Any]]) -> bool:
        return not set(qids) - {_qid(row) for row in rows}

    rows = _run_query(session, query, f"page of {len(qids)} works", mirror=True, complete=covered)
    missing = set(qids) - {_qid(row) for row in rows}
    if missing:
        raise RuntimeError(
            f"wikidata page returned {len(qids) - len(missing)} of {len(qids)} requested works; "
            f"missing e.g. {sorted(missing)[:5]}"
        )
    return rows


def _fold_work_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group rows by work QID: its label, composers (QID -> label), premiere
    dates (at their precision) and premiere places."""
    works: dict[str, dict[str, Any]] = {}
    for row in rows:
        qid = _qid(row)
        work = works.setdefault(
            qid, {"label": _literal(row, "itemLabel") or "", "composers": {}, "dates": set(), "places": set()}
        )
        composer = row.get("composer") or {}
        composer_label = _literal(row, "composerLabel")
        if composer.get("type") == "uri" and composer_label and not _BARE_QID.match(composer_label):
            work["composers"][composer["value"].rsplit("/", 1)[-1]] = composer_label
        premiere = _literal(row, "premiere")
        if premiere is not None:
            work["dates"].add(_format_time(premiere, _literal(row, "premierePrecision")))
        place = _literal(row, "premierePlaceLabel")
        if place and not _BARE_QID.match(place):
            work["places"].add(place)
    return works


def _earliest(dates: set[str]) -> str | None:
    """The date that wins under the premiere precedence rule."""
    best: str | None = None
    for date in sorted(dates):
        if supersedes(date, best):
            best = date
    return best


def _mentions_from_rows(rows: list[dict[str, Any]], ingested_at: datetime) -> list[WorkMentionDocument]:
    """One work mention per (work, composer) with a nameable title, composer
    and premiere date."""
    mentions: list[WorkMentionDocument] = []
    for qid, work in _fold_work_rows(rows).items():
        title: str = work["label"]
        if not title or _BARE_QID.match(title):
            log.debug("skipping %s: no label", qid)
            continue
        date = _earliest(work["dates"])
        if date is None:
            log.debug("skipping %s: no usable premiere date", qid)
            continue
        places = sorted(work["places"])
        premiere = {"date": date, "place": "; ".join(places) or None}
        for composer_qid, composer in sorted(work["composers"].items()):
            mentions.append(
                WorkMentionDocument(
                    # keyed by composer too, so a collaboration's mentions stay
                    # distinct and an added co-composer doesn't rename the rest
                    id=f"{qid}:{composer_qid}",
                    url=f"{BASE_URL}/wiki/{qid}",
                    source_name=WikidataWorksAdapter.name,
                    ingested_at=ingested_at,
                    title=title,
                    composer=composer,
                    raw={
                        "item": f"http://www.wikidata.org/entity/{qid}",
                        "composer_item": f"http://www.wikidata.org/entity/{composer_qid}",
                        "premiere_dates": sorted(work["dates"]),
                        "premiere_places": places,
                        PREMIERE_KEY: premiere,
                    },
                )
            )
    return mentions


class WikidataWorksAdapter(HttpSourceAdapter[WorkMentionDocument]):
    name = "wikidata_works"
    base_url = BASE_URL
    cadence = RefreshCadence.YEARLY
    request_delay_s = REQUEST_DELAY_S
    timeout_s = TIMEOUT_S

    def scrape(self, session: SourceSession, max_pages: int | None = None) -> Iterator[WorkMentionDocument]:
        """Yield a mention for every work Wikidata records a premiere for.
        ``max_pages`` caps the number of batches, for test runs; ids are
        numerically ordered, so a capped run sees the longest-known works."""
        qids = _fetch_work_qids(session)
        if not qids:
            raise RuntimeError("wikidata returned no works with a premiere date")
        batches = [qids[i : i + PAGE_SIZE] for i in range(0, len(qids), PAGE_SIZE)]
        if max_pages is not None and max_pages < len(batches):
            log.info("stopping after max_pages=%d", max_pages)
            batches = batches[:max_pages]
        log.info("wikidata_works: %d works in %d pages", len(qids), len(batches))

        for page, batch in enumerate(batches, start=1):
            mentions = _mentions_from_rows(_fetch_work_page(session, batch), datetime.now(UTC))
            log.info("wikidata_works page %d/%d: %d mentions", page, len(batches), len(mentions))
            yield from mentions
