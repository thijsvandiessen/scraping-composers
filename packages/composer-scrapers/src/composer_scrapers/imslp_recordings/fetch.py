"""HTTP access to IMSLP's commercial recordings, through ``api.php``.

The recordings live one per work page, and there are 26,933 of those in
``Category:Pages with commercial recordings`` — so this is a one-request-per-
record source, and the request is worth choosing carefully. Two things settle it
in favour of the API over ``GET /wiki/<Title>``:

* **Half the bytes.** The rendered page is 13.8KB on the wire against 6.2KB for
  ``action=parse&prop=text`` — the difference is ads, navigation, the skin's 4KB
  ``IMSLPMsg`` blob, and nothing this source reads. Over a full sweep that is
  ~165MB rather than ~370MB.
* **Page ids instead of titles.** ``list=categorymembers`` hands back a
  ``pageid`` beside every title and ``action=parse`` takes one, so the titles in
  this category that are a minefield to encode — ``'E spingole frangese!``,
  ``1.X.1905 (Janáček, Leoš)`` — never have to be. The response names the page
  back, so the work title and its composer suffix are read from the API's own
  answer rather than trusted from the listing.

``robots.txt`` disallows ``/index.php``, ``/wiki/Special:`` and ``/images/``;
``/api.php`` is not disallowed, and asks for ``Crawl-delay: 2``.

What the API cannot do is batch. This is MediaWiki 1.18 — ``action=parse`` takes
one page per call, and the IMSLP extensions register no API action of their own
— so the sweep is 26,933 calls however it is written, and the way to pay for it
once is :class:`~composer_http.PageCache`. A run interrupted after two hours
resumes where it stopped rather than starting over.

One more 1.18-ism: continuation comes back under ``query-continue``, not the
``continue`` of every modern MediaWiki.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

import httpx
from composer_http import PageCache, get_json, get_text, new_client, open_page_cache

log = logging.getLogger(__name__)

BASE_URL = "https://imslp.org"
API_URL = BASE_URL + "/api.php"

#: Both collections IMSLP holds — Naxos (25,943 pages) and BnF (6,368) — merge
#: into the one ``JGCommRec`` array, so walking the parent category covers both
#: and walking the subcategories would only fetch the overlap twice.
CATEGORY = "Category:Pages with commercial recordings"

#: Category members per listing call. The API's ceiling for an anonymous client,
#: and it turns 26,933 members into ~54 requests.
PAGE_SIZE = 500

#: Between uncached requests. Half of the ``Crawl-delay: 2`` robots.txt asks
#: for, matching what :mod:`composer_scrapers.imslp_works` and
#: :mod:`composer_scrapers.wienerphil` already do against per-record pages —
#: against a payload half the size of the one they fetch.
REQUEST_DELAY_S = 1.0

RETRIES = 3

#: Kept unescaped in a permalink. IMSLP writes these literally in its own links,
#: and percent-encoding them only makes a stored url harder to read.
_TITLE_SAFE = "_,()'!:"


def members_url(cmcontinue: str | None = None) -> str:
    """One page of the category listing."""
    url = (
        f"{API_URL}?action=query&list=categorymembers"
        f"&cmtitle={quote(CATEGORY, safe='')}&cmlimit={PAGE_SIZE}&format=json"
    )
    return url if cmcontinue is None else f"{url}&cmcontinue={quote(cmcontinue, safe='')}"


def parse_url(pageid: int) -> str:
    """The parser output for one page — where ``JGCommRec`` lives."""
    return f"{API_URL}?action=parse&pageid={pageid}&prop=text&format=json"


def page_url(title: str) -> str:
    """The human permalink for *title*, which is what a mention should link to.

    Not the API call: a reader following a stored url wants the work page, and
    nothing downstream re-fetches from it.
    """
    path = quote(title.replace(" ", "_"), safe=_TITLE_SAFE)
    return f"{BASE_URL}/wiki/{path}"


def iter_category_members(client: httpx.Client) -> Iterator[tuple[int, str]]:
    """Every ``(pageid, title)`` in the category, following its continuation."""
    cmcontinue: str | None = None
    seen = 0
    while True:
        body = get_json(
            client, members_url(cmcontinue), label=f"category members from {seen}", retries=RETRIES
        )
        members = _members(body)
        for member in members:
            pageid = member.get("pageid")
            title = member.get("title")
            if isinstance(pageid, int) and isinstance(title, str):
                seen += 1
                yield pageid, title
        cmcontinue = _cmcontinue(body)
        if cmcontinue is None:
            log.info("imslp_recordings: %d pages in %s", seen, CATEGORY)
            return


def iter_recording_pages(max_pages: int | None = None) -> Iterator[tuple[int, str, str, str]]:
    """Walk the category, yielding ``(pageid, title, url, parser html)`` per page.

    ``max_pages`` caps the number of ``parse`` calls, which is what a smoke run
    wants to bound; the ~54 listing calls are cheap by comparison and are not
    counted.

    A page that fails is logged and skipped, never raised: a sweep this long
    must not die on one bad page, and IMSLP has been seen to answer a detail
    request with a bot-check interstitial (see :mod:`..imslp_works.fetch`).
    """
    cache = open_page_cache()
    with new_client() as client:
        fetched = 0
        skipped = 0
        for pageid, listed_title in iter_category_members(client):
            if max_pages is not None and fetched >= max_pages:
                log.info("imslp_recordings: stopping after max_pages=%d parsed pages", max_pages)
                break
            parsed = _parse_page(client, cache, pageid, listed_title)
            if parsed is None:
                skipped += 1
                continue
            title, document = parsed
            fetched += 1
            yield pageid, title, page_url(title), document
    log.info(
        "imslp_recordings: %d pages parsed, %d skipped; page mirror: %s",
        fetched,
        skipped,
        cache.summary() if cache is not None else "off",
    )


def _parse_page(
    client: httpx.Client, cache: PageCache | None, pageid: int, listed_title: str
) -> tuple[str, str] | None:
    """``(title, parser html)`` for one page, from the mirror or the API."""
    url = parse_url(pageid)
    mirrored = cache.get(url) if cache is not None else None
    if mirrored is not None:
        return _read(mirrored, listed_title)
    time.sleep(REQUEST_DELAY_S)
    try:
        raw = get_text(client, url, label=f"page {pageid} ({listed_title})", retries=RETRIES)
    except httpx.HTTPError as exc:
        log.warning("imslp_recordings: skipping %r after error (%s)", listed_title, exc)
        return None
    parsed = _read(raw, listed_title)
    # Only a usable answer is mirrored. MediaWiki 1.18 answers an unknown page
    # with a 200 and an error object, so caching whatever decoded as JSON would
    # mean never retrying a page that failed once.
    if parsed is not None and cache is not None:
        cache.put(url, raw)
    return parsed


def _read(raw: str, listed_title: str) -> tuple[str, str] | None:
    """``(title, parser html)`` out of one API response body, or None.

    The title is the API's own, which is what the page is *now* — a listing
    entry can name a page that has since been renamed.
    """
    try:
        body = json.loads(raw)
    except ValueError as exc:
        log.warning("imslp_recordings: unreadable API response for %r (%s)", listed_title, exc)
        return None
    parse = body.get("parse") if isinstance(body, dict) else None
    if not isinstance(parse, dict):
        error = body.get("error") if isinstance(body, dict) else None
        log.warning("imslp_recordings: no parse output for %r (%s)", listed_title, error)
        return None
    text = parse.get("text")
    document = text.get("*") if isinstance(text, dict) else None
    if not isinstance(document, str):
        log.warning("imslp_recordings: empty parser output for %r", listed_title)
        return None
    title = parse.get("title")
    return (title if isinstance(title, str) else listed_title), document


def _members(body: dict[str, Any]) -> list[dict[str, Any]]:
    query = body.get("query")
    members = query.get("categorymembers") if isinstance(query, dict) else None
    return [member for member in members if isinstance(member, dict)] if isinstance(members, list) else []


def _cmcontinue(body: dict[str, Any]) -> str | None:
    """The continuation token, under MediaWiki 1.18's ``query-continue``."""
    section = body.get("query-continue")
    members = section.get("categorymembers") if isinstance(section, dict) else None
    token = members.get("cmcontinue") if isinstance(members, dict) else None
    return token if isinstance(token, str) and token else None
