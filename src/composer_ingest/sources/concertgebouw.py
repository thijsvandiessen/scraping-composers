"""Concertgebouworkest concert archive (archief.concertgebouworkest.nl).

The archive's search form embeds its complete filter dropdowns in the page
HTML: one ``<select>`` each for composers, conductors, and soloists. A single
fetch of the search page therefore yields all three lists — there is no
pagination, and no per-person page (the form is POST-only), so records carry
no URL. Composer labels end in life years ("Abert, Johann Joseph (1832 -
1915)", "(1970)" for the living, "( ? - 1882)" when the birth year is
unknown); soloist labels end in the instrument or voice type in Dutch
("viool", "sopraan"); conductor labels are plain names.
"""

from __future__ import annotations

import html
import logging
import re
import time
from collections.abc import Iterator

import httpx

from . import SourceClaim, SourceRecord

NAME = "concertgebouw"
BASE_URL = "https://archief.concertgebouworkest.nl"

SEARCH_URL = BASE_URL + "/en/archive/search/"
RETRIES = 3

log = logging.getLogger(__name__)

# select element id -> the profession the source asserts by listing a person
# in that dropdown
SELECTS: tuple[tuple[str, str], ...] = (
    ("componistcode", "composer"),
    ("dirigentcode", "conductor"),
    ("solistcode", "soloist"),
)

# "(1832 - 1915)", "(1970)" (birth only), "( ? - 1882)" (birth unknown)
_LIFE_YEARS = re.compile(r"\(\s*(?:(\d{4})|\?)?\s*(?:-\s*(\d{4}))?\s*\)$")
# "(viool)", "(mezzosopraan)", ...
_DISCIPLINE = re.compile(r"\(([^()]+)\)$")
# values are mostly unquoted (value=1427); the "<geen selectie>" placeholder
# is quoted (value="0")
_OPTION = re.compile(r'<option[^>]*\bvalue="?(\d+)"?[^>]*>(.*?)</option>', re.DOTALL)


def _fetch_search_page() -> str:
    with httpx.Client(
        headers={"User-Agent": "composer-ingest/0.1 (research; thijsvandiessen@gmail.com)"},
        timeout=30,
    ) as client:
        for attempt in range(1, RETRIES + 1):
            try:
                resp = client.get(SEARCH_URL)
                resp.raise_for_status()
                return resp.text
            except httpx.HTTPError as exc:
                if attempt == RETRIES:
                    raise
                wait = 2**attempt
                log.warning("search page fetch failed (%s), retrying in %ds", exc, wait)
                time.sleep(wait)
    raise AssertionError("unreachable")


def _options(page: str, select_id: str) -> Iterator[tuple[str, str]]:
    """Yield (value, unescaped label) for each real option of the select."""
    block = re.search(rf'<select id="{select_id}".*?</select>', page, re.DOTALL)
    if block is None:
        raise ValueError(f"select #{select_id} not found in search page; did the site change?")
    for value, label in _OPTION.findall(block.group(0)):
        if value == "0":  # the "<geen selectie>" placeholder
            continue
        yield value, html.unescape(label).strip()


def _record(select_id: str, profession: str, value: str, label: str) -> SourceRecord | None:
    claims = [SourceClaim("has_profession", "profession", profession)]
    name = label
    if select_id == "componistcode":
        years = _LIFE_YEARS.search(label)
        if years:
            name = label[: years.start()].strip()
            if years.group(1):
                claims.append(SourceClaim("born_on", value=years.group(1)))
            if years.group(2):
                claims.append(SourceClaim("died_on", value=years.group(2)))
    elif select_id == "solistcode":
        discipline = _DISCIPLINE.search(label)
        if discipline:
            name = label[: discipline.start()].strip()
            claims.append(SourceClaim("performs_as", "discipline", discipline.group(1).strip()))
    if not name:
        return None
    return SourceRecord(
        external_id=f"{select_id}:{value}",
        name=name,
        url=None,
        raw={"select": select_id, "value": value, "label": label},
        claims=tuple(claims),
    )


def fetch_records(max_pages: int | None = None) -> Iterator[SourceRecord]:
    """Yield every composer, conductor, and soloist in the archive's search
    filters. The whole source is one page; ``max_pages`` is accepted for
    interface compatibility and ignored."""
    page = _fetch_search_page()
    for select_id, profession in SELECTS:
        count = 0
        for value, label in _options(page, select_id):
            record = _record(select_id, profession, value, label)
            if record is not None:
                count += 1
                yield record
        log.info("concertgebouw %s: %d records", select_id, count)
