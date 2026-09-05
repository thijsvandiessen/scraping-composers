"""Parsing an ``/events/<slug>`` page.

The page carries the thing this source is worth scraping for: a server-rendered
programme in which LA Phil itself names the composer of every work performed.

    <h4 class="program-item__header">
      <a href="/people/manuel-de-falla"
         class="program-item__composer program-item__composer--link">FALLA</a>
    </h4>
    <div class="program-item__body">
      <div class="program-item__piece">
        <a href="/works/ritual-fire-dance" class="program-item__title …"><em>Ritual Fire Dance</em></a>

That anchor is an assertion by the source, not an inference: whoever is linked
there wrote the piece. The credit is sometimes a bare ``<span>`` instead — a
display-only surname with no page behind it (``EWALD``, ``BOWEN``, and the
catch-all ``VARIOUS``) — so ``composer_slug`` is optional.

Composer names here are printed in a house style that surnames in caps and
often drops the forename entirely ("BEETHOVEN", "Gabriela ORTIZ", "J.S. BACH").
That is a *display* name; the linked person page holds the real one.

Only the composer half of a :class:`ProgramItem` is read today. The rest — work
slug and title, duration, premiere/commission notes — and the whole artist list
are parsed anyway, because the mirrored HTML they come from is the input a later
concerts pass will want and this is where it is already understood.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .text import text
from .urls import canonical, slug

# One programme entry. The section is a flat run of these, so a block is
# everything up to the next one; splitting beats trying to balance <div>s.
_ITEM_SPLIT_RE = re.compile(r'<div class="program-item[\s"]')
_PROGRAM_SECTION_RE = re.compile(r'<section[^>]*class="[^"]*\bprogram-block\b[^"]*"(.*)', re.S)

_COMPOSER_RE = re.compile(
    r'<(a|span)([^>]*\bclass="[^"]*\bprogram-item__composer\b[^"]*"[^>]*)>(.*?)</\1>', re.S
)
_TITLE_RE = re.compile(r'<(a|span)([^>]*\bclass="[^"]*\bprogram-item__title\b[^"]*"[^>]*)>(.*?)</\1>', re.S)
_DURATION_RE = re.compile(r'<div class="program-item__duration">(.*?)</div>', re.S)
_NOTE_RE = re.compile(r'<div class="program-item__underwriting">(.*?)</div>', re.S)
_HREF_RE = re.compile(r'href="([^"]+)"')

# One performer credit in the "Artists" section.
_ARTIST_SPLIT_RE = re.compile(r'<div class="artist-list__person">')
_ARTIST_NAME_RE = re.compile(r'<h3 class="artist-item__title">(.*?)</h3>', re.S)
_ARTIST_ROLE_RE = re.compile(r'<p class="artist-item__role">(.*?)</p>', re.S)


@dataclass(frozen=True)
class ProgramItem:
    """One work on the programme, as the page prints it."""

    composer_slug: str | None
    composer_display: str
    work_slug: str | None
    work_title: str
    duration: str
    note: str


@dataclass(frozen=True)
class ArtistCredit:
    """One performer in the "Artists" section, with the role the page gives them."""

    slug: str | None
    name: str
    role: str


def _linked_slug(attrs: str) -> str | None:
    """The ``/people/`` or ``/works/`` slug an opening tag links to, if any."""
    match = _HREF_RE.search(attrs)
    if match is None:
        return None
    href = match.group(1).split("?", 1)[0].split("#", 1)[0].rstrip("/")
    for section in ("/people/", "/works/"):
        if section in href:
            return href.rsplit("/", 1)[-1] or None
    return None


def _program_blocks(page_html: str) -> list[str]:
    """The page's programme entries as raw markup, one string each.

    Scoped to the ``program-block`` section first so the "you may also like"
    carousels below it cannot contribute phantom entries.
    """
    section = _PROGRAM_SECTION_RE.search(page_html)
    if section is None:
        return []
    return _ITEM_SPLIT_RE.split(section.group(1))[1:]


def program_items(page_html: str) -> list[ProgramItem]:
    """Every programme entry on the page, in printed order.

    A block with no composer credit at all is skipped: the programme uses the
    same markup for structural rows such as "Intermission".
    """
    items: list[ProgramItem] = []
    for block in _program_blocks(page_html):
        composer = _COMPOSER_RE.search(block)
        if composer is None:
            continue
        display = text(composer.group(3))
        if not display:
            continue
        title = _TITLE_RE.search(block)
        duration = _DURATION_RE.search(block)
        note = _NOTE_RE.search(block)
        items.append(
            ProgramItem(
                composer_slug=_linked_slug(composer.group(2)),
                composer_display=display,
                work_slug=_linked_slug(title.group(2)) if title is not None else None,
                work_title=text(title.group(3)) if title is not None else "",
                duration=text(duration.group(1)) if duration is not None else "",
                note=text(note.group(1)) if note is not None else "",
            )
        )
    return items


def artist_credits(page_html: str) -> list[ArtistCredit]:
    """Every performer credited in the "Artists" section, in printed order.

    Unused by the composer pass — the programme, not this list, is what says who
    composed something — and kept for the concerts pass that will need it.
    """
    credits: list[ArtistCredit] = []
    for block in _ARTIST_SPLIT_RE.split(page_html)[1:]:
        name = _ARTIST_NAME_RE.search(block)
        if name is None:
            continue
        role = _ARTIST_ROLE_RE.search(block)
        href = _HREF_RE.search(block)
        url = canonical(href.group(1)) if href is not None else None
        credits.append(
            ArtistCredit(
                slug=slug(url) if url is not None else None,
                name=text(name.group(1)),
                role=text(role.group(1)) if role is not None else "",
            )
        )
    return credits


def composer_slugs(page_html: str) -> dict[str, str]:
    """``slug -> display name`` for every *linked* composer credited on the page.

    First spelling wins when a composer appears twice on one programme.
    """
    found: dict[str, str] = {}
    for item in program_items(page_html):
        if item.composer_slug is not None and item.composer_slug not in found:
            found[item.composer_slug] = item.composer_display
    return found
