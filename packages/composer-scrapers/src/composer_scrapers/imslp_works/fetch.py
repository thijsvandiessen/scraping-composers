"""HTTP access to imslp.org for a composer's work list and each work's page.

Unlike ``imslp/fetch.py`` (which walks IMSLP's own global people list), this
module never discovers composers from IMSLP: it walks gold's own composer
list (``.gold``) against IMSLP, resolving each to their category page.

A composer's category page (e.g.
``https://imslp.org/wiki/Category:Bach,_Johann_Sebastian``) groups their
works into sections, each introduced by ``<h3 class='nojs'>Section
(count)</h3>`` immediately followed by ``<h2>Section ...: name</h2>``:
"Compositions", "Collaborations", "Pasticcios", "Collected Works", and (for
some composers) more. Only "Compositions" and "Collected Works" are walked
here (chosen with the user — the other sections are works by other people
that merely mention this composer).

Each section is its own MediaWiki category listing under the hood — verified
live against imslp.org while building this: "Compositions" is the page's own
category (its "next 200" link targets the same title as the page), while
e.g. "As Arranger" is a distinct subcategory (``Category:X/Arranger``) with
its own title and independent pagination. A followed pagination link can
therefore land back on the full composer page (same title) or on a bare
single-category listing page (a suffixed title) — ``_iter_one_section``
handles both by re-splitting into sections and falling back to the whole
page when the section heading isn't present there.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
from html import unescape
from urllib.parse import quote

import httpx
from composer_http import get_text, new_client

from .gold import GoldComposer
from .gold import composers as gold_composers

BASE_URL = "https://imslp.org"

#: Section headings (as they appear in "<h3 class='nojs'>NAME (count)</h3>")
#: whose works count as "this composer's works" here.
SECTION_HEADINGS = ("Compositions", "Collected Works")

REQUEST_DELAY_S = 1.0
RETRIES = 3
#: Guard against a pagination link that cycles back on itself.
MAX_SECTION_PAGES = 50

log = logging.getLogger(__name__)

_SECTION_SPLIT = re.compile(r"<h3 class=['\"]nojs['\"]>\s*([^(<]+?)\s*\([\d,]+\)\s*</h3>")
_NEXT_200 = re.compile(
    r'<a\b[^>]*\bhref="([^"]+)"[^>]*\bclass="categorypaginglink"[^>]*>\s*next 200', re.IGNORECASE
)
_WIKI_LINK = re.compile(r'href="/wiki/([^"?#]+)"')
_EXCLUDED_PREFIXES = ("Category:", "Special:", "File:", "Template:", "Help:", "Talk:", "IMSLP:", "Main_Page")


def category_url(label: str) -> str:
    """The category URL IMSLP would use for a person labelled *label*.

    IMSLP category names are literally "Surname, Given", matching gold's own
    label format, so this is a plain construction rather than a search — see
    ``resolve_category_url`` for how the guess is verified before use.
    """
    return f"{BASE_URL}/wiki/Category:{quote(label.replace(' ', '_'), safe='_,()')}"


def resolve_category_url(client: httpx.Client, label: str, known_url: str | None) -> str | None:
    """The composer's category URL, or ``None`` if it can't be confirmed.

    Uses *known_url* (already confirmed by an earlier IMSLP scrape) when
    given; otherwise constructs a candidate from *label* and verifies it
    resolves to a real page before trusting it — a wrong guess must never be
    silently treated as a match.
    """
    url = known_url or category_url(label)
    try:
        resp = client.get(url)
    except httpx.HTTPError as exc:
        log.warning("imslp_works: could not resolve category for %r (%s)", label, exc)
        return None
    if resp.status_code != 200:
        log.info("imslp_works: no category page for %r (%s -> %d)", label, url, resp.status_code)
        return None
    return url


def _sections(html: str) -> dict[str, str]:
    """Split a category page into ``{section_name: section_html}``."""
    matches = list(_SECTION_SPLIT.finditer(html))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(html)
        sections[name] = html[start:end]
    return sections


def _next_200_url(html: str) -> str | None:
    match = _NEXT_200.search(html)
    if match is None:
        return None
    href = unescape(match.group(1))
    if href.startswith("http"):
        return href
    return BASE_URL + href if href.startswith("/") else f"{BASE_URL}/{href}"


def work_paths(section_html: str) -> list[str]:
    """Every work page path in one section's HTML, deduplicated, in order."""
    seen: set[str] = set()
    paths: list[str] = []
    for name in _WIKI_LINK.findall(section_html):
        if name.startswith(_EXCLUDED_PREFIXES):
            continue
        if name not in seen:
            seen.add(name)
            paths.append(name)
    return paths


def _iter_one_section(client: httpx.Client, heading: str, first_chunk: str, start_url: str) -> Iterator[str]:
    seen: set[str] = set()
    # Seeded with the page we've already fetched, so a "next 200" link that
    # points right back at it (self-referential pagination) is caught before
    # a second, wasted request rather than after one.
    seen_urls: set[str] = {start_url}
    chunk = first_chunk
    for _ in range(MAX_SECTION_PAGES):
        for path in work_paths(chunk):
            if path not in seen:
                seen.add(path)
                yield path
        next_url = _next_200_url(chunk)
        if next_url is None or next_url in seen_urls:
            return
        seen_urls.add(next_url)
        time.sleep(REQUEST_DELAY_S)
        page_html = get_text(client, next_url, label=f"{heading} continuation {next_url}", retries=RETRIES)
        resections = _sections(page_html)
        chunk = resections.get(heading, page_html)


def iter_section_work_paths(client: httpx.Client, category_page_url: str) -> Iterator[str]:
    """Every work path across the composer's Compositions + Collected Works
    sections, following each section's own pagination."""
    html = get_text(client, category_page_url, label=f"category {category_page_url}", retries=RETRIES)
    sections = _sections(html)
    for heading in SECTION_HEADINGS:
        chunk = sections.get(heading)
        if chunk is None:
            continue
        yield from _iter_one_section(client, heading, chunk, category_page_url)


def iter_work_pages(
    gold_db_path: str, max_pages: int | None = None
) -> Iterator[tuple[GoldComposer, str, str, str]]:
    """Walk gold's composer list against IMSLP, yielding ``(composer, path,
    url, html)`` per work detail page.

    ``max_pages`` caps the number of *detail* fetches, across all composers
    combined — what a test run wants to bound; category listing pages are
    cheap by comparison.
    """
    with new_client() as client:
        people = gold_composers(gold_db_path)
        log.info("imslp_works: %d composers from gold", len(people))
        fetched = 0
        resolved = 0
        for composer in people:
            url = resolve_category_url(client, composer.label, composer.known_imslp_url)
            if url is None:
                continue
            resolved += 1
            try:
                for path in iter_section_work_paths(client, url):
                    if max_pages is not None and fetched >= max_pages:
                        log.info("imslp_works: stopping after max_pages=%d work pages", max_pages)
                        return
                    time.sleep(REQUEST_DELAY_S)
                    work_url = f"{BASE_URL}/wiki/{path}"
                    html = get_text(client, work_url, label=f"work {path}", retries=RETRIES)
                    yield composer, path, work_url, html
                    fetched += 1
            except httpx.HTTPError as exc:
                # A single composer's work list must not abort the whole
                # gold-driven walk — e.g. IMSLP's own bot-check interstitial
                # (a redirect to /friendlytest.html) has been hit mid-section.
                log.warning("imslp_works: aborting %r's work list after error (%s)", composer.label, exc)
        log.info(
            "imslp_works: %d/%d composers resolved, %d work pages fetched", resolved, len(people), fetched
        )
