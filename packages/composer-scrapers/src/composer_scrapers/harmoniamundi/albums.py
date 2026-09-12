"""One ``/en/albums/<slug>/`` page, read as a release.

Every field the previous LLM crawl of this site guessed at is present here in a
named class, which is the whole reason for this module: ``div.feature.ref`` is
the catalogue number, ``time.release_date`` is the release month,
``div.album_humans`` is the credits list with one role per person. Nothing needs
to be inferred from prose.

Three quirks shape the code.

**``cd_number`` is a format, not a count.** It holds ``"1 CD"``, ``"2 CD"`` or
``"Digital"``, so it is read into ``format`` and never parsed as a number.

**Forthcoming releases have empty features.** An album announced before it is
pressed has no catalogue number, no format and no duration — the ``div``s are
there and empty. Those become None rather than ``""``, because a downstream
merge treats a present-but-empty catalogue number as a conflicting one.

**The ``h1`` is three fields in one.** It holds a composer display string and a
performer display string in spans, with the album title as the bare text
between them. Both spans are display strings for the whole release — the
composer one reads ``"Anthology"`` on a compilation — so they are kept as
strings and never mined for entities. The authoritative credits are in
``div.album_humans``, which names each person separately, with their role, and
links the ones who have a page of their own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from composer_schema import resolve_entity_kind

from .contents import ComposerBlock, fold, parse_contents
from .text import lines, text
from .urls import profile_ref, slug

_H1_RE = re.compile(r'<h1 class="album_title">(.*?)</h1>', re.S)
_COMPOSERS_SPAN_RE = re.compile(r'<span class="compositeurs">(.*?)</span>', re.S)
_ARTISTS_SPAN_RE = re.compile(r'<span class="artistes[^"]*">(.*?)</span>', re.S)

#: ``ref``, ``cd_number`` and ``duration`` all render as a ``div.feature``; the
#: second class says which. ``duration`` carries an inline ``<svg>`` icon before
#: its text, which is why the value goes through :func:`~.text.text`.
_FEATURE_RE = re.compile(r'<div class="feature (ref|cd_number|duration)">(.*?)</div>', re.S)

#: The ``datetime`` attribute is always empty, so the text is the only source.
_RELEASE_DATE_RE = re.compile(r'<time[^>]*class="[^"]*\brelease_date\b[^"]*"[^>]*>(.*?)</time>', re.S)

_HUMANS_START = '<div class="album_humans'
_HUMANS_LIST = '<div class="humans_list">'
_H2_RE = re.compile(r"<h2>(.*?)</h2>", re.S)
_LI_RE = re.compile(r"<li>(.*?)</li>", re.S)
_HUMAN_RE = re.compile(r'<div class="human as_h4">(.*?)</div>', re.S)
_ROLE_RE = re.compile(r'<div class="role as_h5">(.*?)</div>', re.S)
_HREF_RE = re.compile(r'href="([^"]+)"')

_DESCRIPTION_START = '<div class="harmonia_mundi_description"'
_CONTENTS_HEADING_RE = re.compile(r"<h2[^>]*>.*?</h2>", re.S)

_OG_IMAGE_RE = re.compile(r'<meta property="og:image" content="([^"]+)"')

_MONTHS = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}

_MONTH_YEAR_RE = re.compile(r"^([A-Za-z]+)\s+(\d{4})$")
_YEAR_RE = re.compile(r"^(\d{4})$")

#: Credit roles that say what someone *is* rather than what they played.
_NOT_A_DISCIPLINE = frozenset({"", "artist", "artists", "composer", "composers", "conductor", "performer"})

#: Roles the label files a *group* under. Matched exactly, so "Chorus master"
#: stays a person. This is better evidence than the name: ``resolve_entity_kind``
#: reads "NDR Radiophilharmonie" as a person — its vocabulary knows the
#: ``philharmon`` prefix, not a compound ending in ``-philharmonie`` — while the
#: page says "Orchestra" in as many words.
_ENSEMBLE_ROLES = frozenset({"orchestra", "chorus", "choir", "ensemble"})


@dataclass(frozen=True)
class Credit:
    """One person named in the album's credits list."""

    name: str
    role: str
    column: str  # "artists" | "composers"
    ref: tuple[str, str] | None = None  # ("artist"|"composer", slug) when linked

    @property
    def discipline(self) -> str | None:
        """The instrument or voice this credit names, if it names one."""
        return self.role if self.role.casefold() not in _NOT_A_DISCIPLINE else None

    @property
    def is_ensemble(self) -> bool:
        """Whether this credit is a group rather than one musician.

        Decided once, here, so that a participant's role on a recording and the
        kind of the entity behind it can never disagree.
        """
        if self.role.casefold() in _ENSEMBLE_ROLES:
            return True
        return resolve_entity_kind("person", self.name) == "ensemble"


@dataclass(frozen=True)
class Album:
    """One release, as its page states it."""

    slug: str
    url: str
    title: str
    composers_display: str | None
    artists_display: str | None
    catalogue_number: str | None
    release_date: str | None
    format: str | None
    duration: str | None
    credits: tuple[Credit, ...]
    contents: tuple[ComposerBlock, ...]
    contents_text: str | None
    image: str | None

    @property
    def performers(self) -> tuple[Credit, ...]:
        """The credits that are performances — the Artists column.

        A composer is not a performer on their own record, so the Composers
        column is excluded here and carried separately.
        """
        return tuple(credit for credit in self.credits if credit.column == "artists")

    @property
    def composer_credits(self) -> tuple[Credit, ...]:
        """The Composers column — the page's canonical spelling of each composer."""
        return tuple(credit for credit in self.credits if credit.column == "composers")


def release_date(value: str) -> str | None:
    """``"October 2026"`` as ``"2026-10"``, a bare year as itself, else None.

    Normalized rather than passed through because ``recordings.release_date`` is
    text and is *ordered* as text by the consumer API: left as the site writes
    it, every October release would sort between November and September.
    """
    stripped = value.strip()
    match = _MONTH_YEAR_RE.match(stripped)
    if match is not None:
        month = _MONTHS.get(match.group(1).casefold())
        return f"{match.group(2)}-{month}" if month else match.group(2)
    year = _YEAR_RE.match(stripped)
    return year.group(1) if year is not None else None


def _features(page_html: str) -> dict[str, str | None]:
    """``ref``, ``cd_number`` and ``duration``, empty values folded to None."""
    found = {name: text(value) for name, value in _FEATURE_RE.findall(page_html)}
    return {name: found.get(name) or None for name in ("ref", "cd_number", "duration")}


def _title_parts(page_html: str) -> tuple[str, str | None, str | None] | None:
    """The album title, and the two display strings beside it in the ``h1``.

    The title is the heading with both spans removed, rather than a positional
    match on the text between them: the spans are conditional (a compilation
    with no named performers omits the second) and their order has changed.
    """
    heading = _H1_RE.search(page_html)
    if heading is None:
        return None
    fragment = heading.group(1)
    composers = _COMPOSERS_SPAN_RE.search(fragment)
    artists = _ARTISTS_SPAN_RE.search(fragment)
    bare = _ARTISTS_SPAN_RE.sub("", _COMPOSERS_SPAN_RE.sub("", fragment))
    return (
        text(bare),
        text(composers.group(1)) or None if composers else None,
        text(artists.group(1)) or None if artists else None,
    )


def _credit(item_html: str, column: str) -> Credit | None:
    """One ``<li>`` of a credits column."""
    human = _HUMAN_RE.search(item_html)
    if human is None:
        return None
    name = text(human.group(1))
    if not name:
        return None
    role = _ROLE_RE.search(item_html)
    href = _HREF_RE.search(human.group(1))
    return Credit(
        name=name,
        role=text(role.group(1)) if role else "",
        column=column,
        ref=profile_ref(href.group(1)) if href else None,
    )


def _column_name(column_html: str) -> str | None:
    """``"artists"`` / ``"composers"`` for a credits column, else None.

    Matched on the stripped, folded heading because the markup is
    ``<h2>Composers </h2>`` — with the trailing space — and a literal comparison
    silently drops every composer on the site.
    """
    heading = _H2_RE.search(column_html)
    if heading is None:
        return None
    label = text(heading.group(1)).casefold()
    if "composer" in label:
        return "composers"
    return "artists" if "artist" in label else None


def credits(page_html: str) -> tuple[Credit, ...]:
    """Everyone the album credits, in listing order.

    Scoped to ``div.album_humans`` by taking the slice up to the contents block
    that follows it, rather than by balancing ``<div>``s — the page nests them
    four deep around each column and a regex cannot count.

    Deduplicated on name and role: the same person genuinely appears twice in
    one column on some releases (``Nobuyuki Tsujii, Piano`` twice), and loading
    them twice would double their participation on the recording.
    """
    start = page_html.find(_HUMANS_START)
    if start < 0:
        return ()
    end = page_html.find(_DESCRIPTION_START, start)
    scope = page_html[start:end] if end > start else page_html[start:]
    found: list[Credit] = []
    seen: set[tuple[str, str]] = set()
    for column_html in scope.split(_HUMANS_LIST)[1:]:
        column = _column_name(column_html)
        if column is None:
            continue
        for item_html in _LI_RE.findall(column_html):
            credit = _credit(item_html, column)
            if credit is None:
                continue
            key = (fold(credit.name), credit.role.casefold())
            if key in seen:
                continue
            seen.add(key)
            found.append(credit)
    return tuple(found)


def contents_fragment(page_html: str) -> str | None:
    """The tracklist blob, with its "Contents" heading removed.

    The block holds no nested ``div`` — verified across the catalogue — so the
    slice to the next closing tag is the whole field.
    """
    start = page_html.find(_DESCRIPTION_START)
    if start < 0:
        return None
    end = page_html.find("</div>", start)
    fragment = page_html[start:end] if end > start else page_html[start:]
    body = fragment.split(">", 1)[-1]
    return _CONTENTS_HEADING_RE.sub("", body, count=1)


def parse_album(page_html: str, url: str) -> Album | None:
    """The release *page_html* describes, or None when it describes none.

    None for a page with no ``h1.album_title`` — a redirect landing on the album
    search, or a section page reached by a stale sitemap entry.
    """
    parts = _title_parts(page_html)
    if parts is None:
        return None
    title, composers_display, artists_display = parts
    feature = _features(page_html)
    stated = _RELEASE_DATE_RE.search(page_html)
    album_credits = credits(page_html)
    fragment = contents_fragment(page_html)
    image = _OG_IMAGE_RE.search(page_html)
    return Album(
        slug=slug(url),
        url=url,
        title=title,
        composers_display=composers_display,
        artists_display=artists_display,
        catalogue_number=feature["ref"],
        release_date=release_date(stated.group(1)) if stated else None,
        format=feature["cd_number"],
        duration=feature["duration"],
        credits=album_credits,
        contents=(
            parse_contents(
                fragment, [credit.name for credit in album_credits if credit.column == "composers"]
            )
            if fragment
            else ()
        ),
        contents_text="\n".join(lines(fragment)) or None if fragment else None,
        image=image.group(1) if image else None,
    )
