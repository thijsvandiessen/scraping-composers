"""From a Wikipedia article to the Wikidata item it describes.

Deliberately not the Wikidata Query Service: these are lookups by key, a few
hundred requests at most, for which the MediaWiki APIs of Wikipedia
(``prop=pageprops``) and Wikidata (``wbgetentities``, ``wbsearchentities``) are the
right tools, and none shares WDQS's query budget. The first two take 50 keys a request.

A QID is an authority identifier: :mod:`composer_warehouse.persons.constraints`
turns two distinct ones into a hard cannot-link. So a person is pinned only once
the item is confirmed to be them: :func:`confirms_person` for an article the page
itself cites, the stricter :func:`confirms_namesake` for a name search's results.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from composer_http import SourceSession

log = logging.getLogger(__name__)

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
BATCH_SIZE = 50
#: "human".
HUMAN = "Q5"
#: A search's candidates: enough for every namesake of a common name to be compared.
SEARCH_LIMIT = 20
#: Sources routinely disagree by a year; matches the warehouse's own tolerance.
YEAR_TOLERANCE = 1

_YEAR = re.compile(r"^\+(\d{1,4})-")


@dataclass(frozen=True)
class WikidataItem:
    qid: str
    label: str | None
    instance_of: frozenset[str]
    birth_years: frozenset[int]
    death_years: frozenset[int]


def _batches(values: Sequence[str]) -> Iterator[Sequence[str]]:
    for start in range(0, len(values), BATCH_SIZE):
        yield values[start : start + BATCH_SIZE]


def _try_json(session: SourceSession, url: str, label: str) -> dict[str, Any] | None:
    """A failed lookup costs identities, never the crawl."""
    try:
        return session.get_json(url, label=label, mirror=True)
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("classicapedia: %s failed: %s", label, exc)
        return None


def _titles_to_qids(answer: dict[str, Any], asked: Iterable[str]) -> dict[str, str]:
    """Follow the answer's normalisations and redirects back to the asked titles."""
    query: dict[str, Any] = answer.get("query", {})
    renames: dict[str, str] = {}
    for step in ("normalized", "redirects"):
        for rename in query.get(step, []):
            renames[rename["from"]] = rename["to"]
    by_title: dict[str, str] = {}
    for page in query.get("pages", {}).values():
        qid = page.get("pageprops", {}).get("wikibase_item")
        if isinstance(qid, str):
            by_title[page["title"]] = qid
    found: dict[str, str] = {}
    for title in asked:
        current = title
        seen = {current}
        while current in renames and renames[current] not in seen:
            current = renames[current]
            seen.add(current)
        if current in by_title:
            found[title] = by_title[current]
    return found


def wikipedia_qids(session: SourceSession, articles: Iterable[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """The QID of each ``(language, title)`` article that has one."""
    by_language: dict[str, set[str]] = {}
    for language, title in articles:
        by_language.setdefault(language, set()).add(title)
    found: dict[tuple[str, str], str] = {}
    for language, titles in sorted(by_language.items()):
        for batch in _batches(sorted(titles)):
            params = {
                "action": "query",
                "prop": "pageprops",
                "ppprop": "wikibase_item",
                "redirects": "1",
                "format": "json",
                "titles": "|".join(batch),
            }
            url = f"https://{language}.wikipedia.org/w/api.php?{urlencode(params)}"
            answer = _try_json(session, url, f"{language}.wikipedia titles")
            if answer is not None:
                found.update({(language, t): q for t, q in _titles_to_qids(answer, batch).items()})
    return found


def _item_values(claims: dict[str, Any], prop: str) -> list[Any]:
    return [
        statement["mainsnak"]["datavalue"]["value"]
        for statement in claims.get(prop, [])
        if statement.get("rank") != "deprecated" and "datavalue" in statement.get("mainsnak", {})
    ]


def _years(claims: dict[str, Any], prop: str) -> frozenset[int]:
    years: set[int] = set()
    for value in _item_values(claims, prop):
        if isinstance(value, dict) and (match := _YEAR.match(str(value.get("time", "")))) is not None:  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            years.add(int(match.group(1)))
    return frozenset(years)


def parse_item(qid: str, entity: dict[str, Any]) -> WikidataItem:
    claims: dict[str, Any] = entity.get("claims", {})
    label = entity.get("labels", {}).get("en", {}).get("value")
    return WikidataItem(
        qid=qid,
        label=label if isinstance(label, str) else None,
        instance_of=frozenset(
            str(value["id"]) for value in _item_values(claims, "P31") if isinstance(value, dict)
        ),
        birth_years=_years(claims, "P569"),
        death_years=_years(claims, "P570"),
    )


def wikidata_items(session: SourceSession, qids: Iterable[str]) -> dict[str, WikidataItem]:
    """The items *qids* name; a missing or deleted one is left out."""
    found: dict[str, WikidataItem] = {}
    for batch in _batches(sorted(set(qids))):
        params = {
            "action": "wbgetentities",
            "ids": "|".join(batch),
            "props": "labels|claims",
            "languages": "en",
            "format": "json",
        }
        answer = _try_json(session, f"{WIKIDATA_API}?{urlencode(params)}", "wikidata items")
        if answer is None:
            continue
        for qid, entity in answer.get("entities", {}).items():
            if "missing" not in entity:
                found[qid] = parse_item(qid, entity)
    return found


def search_people(session: SourceSession, name: str) -> list[str]:
    """The QIDs of items whose label or alias starts with *name*.

    A prefix on purpose: the site's "Johann Strauss" is Wikidata's "Johann Strauss II".
    The years decide (:func:`confirms_namesake`).
    """
    params = {
        "action": "wbsearchentities",
        "search": name,
        "language": "en",
        "type": "item",
        "limit": str(SEARCH_LIMIT),
        "format": "json",
    }
    answer = _try_json(session, f"{WIKIDATA_API}?{urlencode(params)}", "wikidata search")
    if answer is None:
        return []
    return [str(hit["id"]) for hit in answer.get("search", []) if "id" in hit]


def _agrees(stated: str | None, known: frozenset[int]) -> bool | None:
    """Whether a year fits the item's; ``None`` when one side says nothing."""
    if stated is None or not known:
        return None
    return any(abs(int(stated) - year) <= YEAR_TOLERANCE for year in known)


def confirms_person(item: WikidataItem, birth_year: str | None, death_year: str | None) -> bool:
    """Whether *item* is a human whose years fit, with at least one to compare.

    A page's citation may be an article about a work or a family, and a name
    alone is what two different people look like, so no comparison is no pin.
    """
    if HUMAN not in item.instance_of:
        return False
    verdicts = [_agrees(birth_year, item.birth_years), _agrees(death_year, item.death_years)]
    return True in verdicts and False not in verdicts


def confirms_namesake(item: WikidataItem, birth_year: str | None, death_year: str | None) -> bool:
    """Whether a search result is a human whose birth and death years both fit.

    Stricter than :func:`confirms_person`, since all that links a search result to
    a page is a name known to belong to several people.
    """
    return (
        HUMAN in item.instance_of
        and _agrees(birth_year, item.birth_years) is True
        and _agrees(death_year, item.death_years) is True
    )
