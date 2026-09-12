"""Parsing a ``/people/<slug>`` page.

Two things worth having sit on this page. A JSON-LD ``Person`` node carries the
canonical name, split into given and family names, plus a job title:

    {"@type":"Person","name":"Johannes Brahms","givenName":"Johannes",
     "familyName":"Brahms","jobTitle":"composer","image":"…"}

and the bio block opens, for the composers LA Phil has written one for, with a
fixed two-line preamble:

    <div class="element text-content artist-bio">
      Born: 1833, Hamburg, Germany  Died: 1897, Vienna, Austria  “It is not hard…

Neither field identifies a composer on its own. ``jobTitle`` is absent from the
overwhelming majority of these pages — in a random sample of 40 drawn from the
sitemap, 38 had none and the other two read "Percussion" and "Clarinet" — and
the ``/people/`` section is mostly performers, bands, dancers and staff. It is
the programme credit on an event page (see :mod:`.events`) that says someone is
a composer; this module supplies the name and dates once that is established,
and ``job_title`` corroborates it when the page happens to carry one.

``event_urls`` is what keeps the walk going: a person page links the events they
appeared in — up to ~49 of them, well past what the capped sitemap lists.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from .text import text
from .urls import BASE_URL, canonical, person_url

log = logging.getLogger(__name__)

_JSONLD_RE = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_TITLE_RE = re.compile(r'<h1 class="artist-header__title">(.*?)</h1>', re.S)
_ROLE_RE = re.compile(r'<p class="artist-header__role">(.*?)</p>', re.S)
_BIO_RE = re.compile(r'class="[^"]*\bartist-bio\b[^"]*"[^>]*>(.*?)</div>', re.S)
_ARTIST_ID_RE = re.compile(r"""<favorite-artist\b[^>]*?:id="'([0-9a-f]+)'""", re.S)
_EVENT_HREF_RE = re.compile(r'href="([^"]*/events/[^"]*)"')

#: ``Born: 1833, Hamburg, Germany`` — the year is required, the place optional,
#: and the run ends at the next label or at the pull-quote that follows the
#: preamble (curly or straight quotes, depending on the page).
_BORN_RE = re.compile(r"Born:\s*(\d{4})\s*(?:,\s*([^“”\"]*?))?\s*(?=Died:|[“”\"]|$)")
_DIED_RE = re.compile(r"Died:\s*(\d{4})\s*(?:,\s*([^“”\"]*?))?\s*(?=[“”\"]|$)")


@dataclass(frozen=True)
class PersonPage:
    """One ``/people/`` page, as far as it can be read without inference."""

    slug: str
    name: str
    given_name: str | None
    family_name: str | None
    job_title: str
    image: str | None
    artist_id: str | None
    born_year: str | None
    born_place: str | None
    died_year: str | None
    died_place: str | None
    bio: str
    event_urls: tuple[str, ...]

    @property
    def url(self) -> str:
        return person_url(self.slug)

    @property
    def declares_composer(self) -> bool:
        """Whether the page itself calls this person a composer.

        Corroboration only: a page saying nothing is the normal case here, not a
        denial (see the module docstring).
        """
        return "composer" in self.job_title.casefold()


def _person_node(page_html: str) -> dict[str, Any]:
    """The JSON-LD ``Person`` node, or an empty dict when there is none.

    Malformed JSON-LD is a missing node, never an error: the header markup
    carries the name too, and one bad page must not end a sweep.
    """
    for block in _JSONLD_RE.findall(page_html):
        try:
            graph = json.loads(block)
        except ValueError as exc:
            log.debug("unreadable JSON-LD block (%s); falling back to the header", exc)
            continue
        nodes = graph.get("@graph", [graph]) if isinstance(graph, dict) else graph
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            if isinstance(node, dict) and node.get("@type") == "Person":
                return node
    return {}


def _string(node: dict[str, Any], key: str) -> str:
    value = node.get(key)
    return text(value) if isinstance(value, str) else ""


def _year_and_place(match: re.Match[str] | None) -> tuple[str | None, str | None]:
    """``(year, place)`` from a ``Born:``/``Died:`` match; the place is optional."""
    if match is None:
        return None, None
    place = (match.group(2) or "").strip(" ,")
    return match.group(1), place or None


def _event_urls(page_html: str) -> tuple[str, ...]:
    """Every event page linked here, deduplicated, in document order."""
    urls: list[str] = []
    for href in _EVENT_HREF_RE.findall(page_html):
        url = canonical(href)
        if url is not None and url.startswith(f"{BASE_URL}/events/") and url not in urls:
            urls.append(url)
    return tuple(urls)


def parse_person(slug: str, page_html: str) -> PersonPage | None:
    """Read one person page. ``None`` when it carries no name to read."""
    node = _person_node(page_html)
    name = _string(node, "name")
    if not name:
        header = _TITLE_RE.search(page_html)
        name = text(header.group(1)) if header is not None else ""
    if not name:
        return None

    job_title = _string(node, "jobTitle")
    if not job_title:
        role = _ROLE_RE.search(page_html)
        job_title = text(role.group(1)) if role is not None else ""

    bio_match = _BIO_RE.search(page_html)
    bio = text(bio_match.group(1)) if bio_match is not None else ""
    born_year, born_place = _year_and_place(_BORN_RE.search(bio))
    died_year, died_place = _year_and_place(_DIED_RE.search(bio))
    artist_id = _ARTIST_ID_RE.search(page_html)

    return PersonPage(
        slug=slug,
        name=name,
        given_name=_string(node, "givenName") or None,
        family_name=_string(node, "familyName") or None,
        job_title=job_title,
        image=_string(node, "image") or None,
        artist_id=artist_id.group(1) if artist_id is not None else None,
        born_year=born_year,
        born_place=born_place,
        died_year=died_year,
        died_place=died_place,
        bio=bio,
        event_urls=_event_urls(page_html),
    )
