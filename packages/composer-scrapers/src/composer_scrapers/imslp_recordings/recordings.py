"""Read one IMSLP work page's commercial recordings. No HTTP.

The "Commercial 💿" tab renders client-side — the served markup holds only
``<div id="naxosscroll">Javascript not enabled.</div>`` — but the data behind it
is not a second request. It ships inline as a JS global that IMSLP's own
``IMSLPJS.D.js`` picks up (``window.JGCommRec && JGCommRec.length ?
setupnaxostab(JGCommRec, !0)``), and it sits inside the *parser output*, which
is why ``api.php`` serves it at all (see :mod:`.fetch`).

One entry per album the work appears on::

    JGCommRec=[{"aid":61682,
      "tit":"VAUGHAN WILLIAMS, R.: 10 Blake Songs / Oboe Concerto ...",
      "img":"https://cdn.imslp.org/naxoscache.php?pool=hires&file=C5035.jpg",
      "art":"William Blake (lyricist), Andreas Weller (tenor), Lajos Lencsés (oboe)",
      "trs":[{"dsc":"10 Blake Songs: No. 1. Infant Joy","dur":106,"url":"..."}]}]

Two things about that payload are worth stating, because both shape what the
adapter can claim:

* **``trs`` is scoped to the work whose page this is**, not to the whole album.
  A page for a 12-sonata set lists 25 albums between them and 98 tracks in
  total, four per album. So the track list *is* the work-to-recording link, and
  it is small enough to keep verbatim.
* **``art`` mixes performers with people who wrote the words.** A lyricist is
  not a performer on a recording, so those credits are dropped from the
  participant list the way :mod:`composer_scrapers.decca` drops composers from
  its own — kept in the raw payload, absent from the credits.

``trs[].url`` is a per-session encrypted streaming token and is dropped.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from composer_schema import looks_like_ensemble

log = logging.getLogger(__name__)

#: The JS global the recordings ride in on, as it appears in the parser output.
_MARKER = "JGCommRec="

#: ``Name (role)``. The role never nests, so a lazy inner class is enough, and
#: anchoring at the end lets a name keep parentheses of its own.
_CREDIT = re.compile(r"^(?P<name>.*?)\s*\((?P<role>[^()]*)\)$")

#: The trailing ``(Surname, Given)`` on an IMSLP work page title. Greedy on the
#: left so the *last* group wins, and a comma is required so a title whose own
#: parenthetical is not a composer ("… (arr. for 2 pianos)") is left alone.
_TITLE_COMPOSER = re.compile(r"^(?P<title>.*\S)\s+\((?P<composer>[^()]*,[^()]*)\)$")

#: The Naxos album's identifier inside a ``naxoscache.php`` cover URL:
#: ``?pool=hires&file=C5035.jpg`` -> ``C5035``.
_COVER_FILE = re.compile(r"[?&]file=([^.&/?]+)\.")

#: The BnF collection's covers are served from Petrucci's own mirror under the
#: recording's BnF ark, which is the same kind of thing one directory up:
#: ``/all/12148-bpt6k88103018/coverimage.jpg`` -> ``12148-bpt6k88103018``. The
#: filename itself is always "coverimage", so only the directory identifies.
_COVER_ARK = re.compile(r"petruccimusiclibrary\.ca/[^/?]+/([^/?]+)/")

#: The participant roles ``composer_warehouse.recordings.derive`` recognises.
#: Anything else it folds to "performer", so the mapping below stays inside this
#: vocabulary on purpose rather than inventing labels that quietly collapse.
ROLE_CONDUCTOR = "conductor"
ROLE_SOLOIST = "soloist"
ROLE_ENSEMBLE = "ensemble"
ROLE_PERFORMER = "performer"

#: Roles that credit authorship of the words rather than a performance. These
#: name real people and belong in the payload, but not among the performers.
_NOT_A_PERFORMER = frozenset(
    {"lyricist", "author", "librettist", "poet", "translator", "editor", "arranger", "composer"}
)

#: Roles that say someone took part without saying how. They stay credits, but
#: there is no instrument or voice in them to claim a ``performs_as`` from.
_GENERIC = frozenset(
    {"artist", "featuredartist", "featured artist", "featuring", "misc", "other", "performer", "singer"}
)

#: Roles that already name one of the participant roles above, so they pass
#: through as themselves rather than through the instrument branch.
_NAMED_ROLES = frozenset({ROLE_SOLOIST, ROLE_ENSEMBLE})

_CONDUCTOR = ROLE_CONDUCTOR


@dataclass(frozen=True)
class Credit:
    """One performer on an album, as the credit line named them."""

    name: str
    role: str
    discipline: str | None = None

    @property
    def disciplines(self) -> tuple[str, ...]:
        """The instruments this credit names, for a ``performs_as`` claim each.

        A single credit can list several — "(violin, mandolin)" is one person
        playing two. The participant row keeps the discipline as credited; only
        the claims need them apart.
        """
        if not self.discipline:
            return ()
        return tuple(part.strip() for part in self.discipline.split(",") if part.strip())


@dataclass(frozen=True)
class Track:
    """One track of *this work* on an album."""

    description: str | None
    duration_s: int | None


@dataclass(frozen=True)
class Album:
    """One commercial release the work appears on."""

    album_id: int
    title: str | None
    cover_url: str | None
    credit_line: str | None
    credits: tuple[Credit, ...]
    tracks: tuple[Track, ...]

    @property
    def catalogue_number(self) -> str | None:
        """The release id encoded in the cover URL, when there is one.

        Best-effort, and not always a *printed* catalogue number — a Naxos cover
        names one ("C5035") where a BnF cover names the recording's ark. Both
        are stable per album, which is all ``recordings.cluster`` asks of the
        field: two same-titled releases sharing a performer stay apart when
        their catalogue numbers conflict.
        """
        return cover_id(self.cover_url)


@dataclass(frozen=True)
class WorkPage:
    """One work page's identity: what IMSLP calls it, and what that means."""

    page_id: int
    page_title: str
    url: str
    title: str
    composer: str | None


def work_page(page_id: int, page_title: str, url: str) -> WorkPage:
    """The page context a mention is built from, with its title split."""
    title, composer = split_page_title(page_title)
    return WorkPage(page_id=page_id, page_title=page_title, url=url, title=title, composer=composer)


def split_page_title(title: str) -> tuple[str, str | None]:
    """An IMSLP work page title split into ``(work title, composer)``.

    Every work page is named ``Work Title (Surname, Given)``, so the composer
    comes free with the page. Returns the title unchanged and ``None`` when the
    convention does not hold — a page with no trailing group, or one whose
    trailing group holds no comma and so is part of the title.

    >>> split_page_title("'O sole mio (Di Capua, Eduardo)")
    ("'O sole mio", 'Di Capua, Eduardo')
    """
    match = _TITLE_COMPOSER.match(title.strip())
    if match is None:
        return title.strip(), None
    return match.group("title"), match.group("composer").strip()


def cover_id(cover_url: str | None) -> str | None:
    """The album identifier a cover URL carries, in either collection's form.

    Naxos covers name the release in a query parameter and BnF covers name it in
    the path; a URL of neither shape has no identifier to give and returns None.
    """
    if not cover_url:
        return None
    for pattern in (_COVER_FILE, _COVER_ARK):
        match = pattern.search(cover_url)
        if match is not None:
            return match.group(1)
    return None


def credits(credit_line: str | None) -> tuple[Credit, ...]:
    """The performers named in an ``art`` line, in the order credited.

    The line is ``", "``-joined ``Name (role)``, with the role sometimes absent.
    Roles map onto the four the recordings pass understands:

    ==============================  ===========  ===============
    credited as                     role         discipline
    ==============================  ===========  ===============
    ``conductor``                   conductor    --
    ``piano, conductor``            conductor    piano
    an instrument or a voice        soloist      that instrument
    ``soloist``                     soloist      --
    a group name, with or without   ensemble     --
    a role naming a group
    nothing, or ``other``/``misc``  performer    --
    only ``lyricist``/``author``    *dropped*    --
    ==============================  ===========  ===============
    """
    if not credit_line:
        return ()
    found: list[Credit] = []
    for part in _fragments(credit_line):
        credit = _credit(part.strip())
        if credit is not None:
            found.append(credit)
    return tuple(found)


def _fragments(credit_line: str) -> Iterator[str]:
    """Split an ``art`` line on the separators *between* credits.

    Not a plain ``split(", ")``: a role can list two instruments, and
    "François Pilon (violin, mandolin)" split naively becomes a performer called
    "François Pilon (violin" and another called "mandolin)" — both of which load
    as entities. Only a comma at paren depth zero separates two people.
    """
    depth = 0
    start = 0
    position = 0
    while position < len(credit_line):
        character = credit_line[position]
        if character == "(":
            depth += 1
        elif character == ")":
            depth = max(depth - 1, 0)
        elif depth == 0 and credit_line.startswith(", ", position):
            yield credit_line[start:position]
            position += 2
            start = position
            continue
        position += 1
    yield credit_line[start:]


def _credit(part: str) -> Credit | None:
    """One ``Name (role)`` fragment, or None when it credits a non-performer.

    A fragment that parses to an empty name is dropped rather than credited:
    ``art`` names people in display order ("Jussi Björling"), so a fragment with
    no name left over was punctuation, not a person.
    """
    if not part:
        return None
    match = _CREDIT.match(part)
    name = (match.group("name") if match else part).strip()
    role = (match.group("role") if match else "").strip()
    if not name:
        return None
    resolved = _performer_role(name, role)
    return None if resolved is None else Credit(name, resolved[0], resolved[1])


def _performer_role(name: str, role: str) -> tuple[str, str | None] | None:
    """The ``(role, discipline)`` a credited role maps to, or None to drop it.

    A parenthetical is only *sometimes* an instrument, so it is read as one only
    by elimination. Observed on the live category, in one page's credits:
    ``(piano, conductor)`` (both at once), ``(orchestra)`` and
    ``(Radio-Sinfonie-Orchester Berlin)`` (the group, not what it plays),
    ``(featuring)`` and ``(other)`` (nothing at all), and ``(lyricist)`` (not a
    performance). Only what survives all of those becomes a discipline —
    whitelisting instruments instead would quietly drop "viola da gamba" and
    "boy soprano", which are as real as "piano".
    """
    parts = [part.strip() for part in role.split(",") if part.strip()]
    folded = [part.casefold() for part in parts]
    if folded and all(part in _NOT_A_PERFORMER for part in folded):
        return None
    disciplines = [
        part
        for part, fold in zip(parts, folded, strict=True)
        if fold not in _NOT_A_PERFORMER
        and fold not in _GENERIC
        and fold not in _NAMED_ROLES
        and fold != _CONDUCTOR
        and not looks_like_ensemble(part)
    ]
    if _CONDUCTOR in folded:
        return ROLE_CONDUCTOR, ", ".join(disciplines) or None
    if disciplines:
        return ROLE_SOLOIST, ", ".join(disciplines)
    if ROLE_SOLOIST in folded:
        return ROLE_SOLOIST, None
    group = ROLE_ENSEMBLE in folded or looks_like_ensemble(name) or any(map(looks_like_ensemble, parts))
    return (ROLE_ENSEMBLE if group else ROLE_PERFORMER), None


def commercial_recordings(document: str) -> list[Album]:
    """Every album named in *document*'s ``JGCommRec`` global.

    Empty when the page carries none, which is the common case off this
    category and never an error. Parsed with a JSON decoder reading forward from
    the marker rather than a regex over ``[...]``: the payload nests objects and
    arrays, and only a real decoder finds where it ends.
    """
    index = document.find(_MARKER)
    if index < 0:
        return []
    try:
        payload, _ = json.JSONDecoder().raw_decode(document, index + len(_MARKER))
    except ValueError as exc:
        log.warning("imslp_recordings: unreadable JGCommRec payload (%s)", exc)
        return []
    if not isinstance(payload, list):
        return []
    albums: list[Album] = []
    for entry in payload:
        album = _album(entry)
        if album is not None:
            albums.append(album)
    return albums


def _album(entry: Any) -> Album | None:
    """One ``JGCommRec`` entry, or None without the album id that identifies it."""
    if not isinstance(entry, dict):
        return None
    album_id = entry.get("aid")
    if not isinstance(album_id, int):
        return None
    credit_line = _text(entry.get("art"))
    return Album(
        album_id=album_id,
        title=_text(entry.get("tit")),
        cover_url=_text(entry.get("img")),
        credit_line=credit_line,
        credits=credits(credit_line),
        tracks=_tracks(entry.get("trs")),
    )


def _tracks(value: Any) -> tuple[Track, ...]:
    if not isinstance(value, list):
        return ()
    found: list[Track] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        duration = entry.get("dur")
        found.append(
            Track(
                description=_text(entry.get("dsc")),
                duration_s=duration if isinstance(duration, int) else None,
            )
        )
    return tuple(found)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
