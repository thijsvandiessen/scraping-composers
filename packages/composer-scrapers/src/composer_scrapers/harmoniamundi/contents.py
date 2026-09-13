"""The "Contents" blob on an album page, read as composers, works and tracks.

This is the only place the site states what music is *on* a release, and it is
the one field that is not structured: a hand-edited rich-text box whose
conventions have drifted over the catalogue's life. So the rules here are
deliberately narrow. Two signals turned out to be dependable across the
catalogue and everything else is derived from them:

* **life dates in brackets** mark a composer heading — ``JOHANN SEBASTIAN BACH
  (1685-1750)``, ``MOZART [1756-1791]``, ``JOHN DOWLAND [1562/1563-1626]``;
* **a leading bullet** (``·`` or a bare ``.``) marks a track.

Three things that look like signals and are not, each verified against a real
page rather than assumed:

* ``<strong>`` does not separate works from tracks. On some albums every *track*
  is bolded (``<strong>· Ezio's Family</strong> (3'22)``), on others the work is.
  It carries no information here, which is why :mod:`.text` drops it.
* Composer headings are not reliably upper-case: ``Johann Sebastian Bach
  (1685-1750)`` appears in title case.
* A bullet does not mean "not a composer heading". ``· Leonard BERNSTEIN
  (1918-1990)`` is a heading with a bullet, and ``· Traditional, arr. James ERB
  (1926-2014) (3'53)`` is one with a bullet *and* a duration. Hence the order
  below: the bullet and the trailing duration come off the line first, and what
  is left is tested for life dates before anything else.

What the caller gets is one :class:`ComposerBlock` per composer, holding
:class:`WorkEntry` per work with its movements as :class:`Track`. A bulleted
line under a work heading is a movement of that work; a bulleted line with no
heading above it in its block is a standalone piece and becomes a work of its
own. That grouping is the residual guess — a compilation that bolds a new work
without an unbulleted heading folds it in as a movement of the previous one —
and it is why the adapter also keeps every track, flat, in the raw payload:
nothing is lost from bronze, and a later pass can regroup without re-fetching.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field

from .text import lines

#: Attributions the label uses where a composer would go. These name the absence
#: of a known composer rather than a person, and minting an entity for them would
#: put "Anonymous" into the composer dedupe pass on the strength of 40 albums.
_NOT_A_COMPOSER = frozenset({"anonymous", "anonyme", "anon", "anonymus", "traditional", "various"})

#: ``·`` (and its lookalikes), or a bare ``.`` — the editors use both, and a few
#: albums run the dot straight into the title (``.Moderato scherzando``).
_BULLET_RE = re.compile(r"^[·•∙‧]\s*|^\.(?!\.)\s*")

#: A track's own length, always trailing: ``(5'25)``, ``(11'02)``.
_DURATION_RE = re.compile(r"\s*\((\d{1,2})['’](\d{2})\)\s*$")

#: A year as the editors write it: ``1750``, ``1562/1563``, ``1622/23``, and the
#: occasional full date ``01/01/1621``.
_YEAR = r"(?:\d{1,2}/){0,2}\d{3,4}(?:/\d{2,4})?\??"

#: A hedge before a year: ``fl.`` (floruit), ``ca.``, ``c.``.
_QUALIFIER = r"(?:[a-zA-Z]{1,2}\.?\s*)?"

#: A composer heading: a name, then life dates in round or square brackets.
#: Either year may be absent — ``(1959 - )`` for a living composer, ``(-1626)``
#: where only a death is known — but not both, which is what keeps a bare
#: parenthetical like ``(1965)`` from reading as a heading.
_LIFE_RE = re.compile(
    rf"^(?P<name>[^()\[\]]{{2,80}}?)\s*[(\[]\s*"
    rf"{_QUALIFIER}(?P<born>{_YEAR})?\s*[-\u2013\u2014]\s*{_QUALIFIER}(?P<died>{_YEAR})?\s*"
    rf"[)\]]$"
)

#: A heading that states one date rather than a span: ``STEVE REICH (b.1936)``
#: for a living composer, ``(d. 1750)`` where only a death is recorded. These
#: carry no dash, so ``_LIFE_RE`` cannot see them.
_SINGLE_DATE_RE = re.compile(
    rf"^(?P<name>[^()\[\]]{{2,80}}?)\s*[(\[]\s*(?P<mark>[bd])\.?\s*(?P<year>{_YEAR})\s*[)\]]$"
)

#: A carrier marker: ``CD2``, ``FACE B``, ``SIDE B``. The editors use these to
#: head a disc or a vinyl side. They are packaging, not music, and read as work
#: headings they mint canonical works called "FACE B". A trailing number or
#: letter is required, so a piece actually titled "Face" survives.
_CARRIER_RE = re.compile(r"^(?:CD|DISC|DISQUE|DISK|FACE|SIDE|LP)\s*\.?\s*(?:\d+|[IVX]+|[A-D])$", re.I)

#: A line that is wholly a parenthetical — ``(Pièces de viole, 1685)``,
#: ``(Manuscrit de Cracovie)``. The editors use these to cite the source or
#: edition a work is taken from; they are notes about the work above, not works.
_NOTE_RE = re.compile(r"^\(.*\)$")

_WORD_RE = re.compile(r"[^\W_]+")


def _year(token: str | None) -> str | None:
    """The four-digit year in a date token, or None.

    The editors write a life date several ways — ``1750``, ``1622/23`` (a year
    spanning a new-year boundary), ``01/01/1621`` (a full date) — and only the
    year is claimed, so the first four-digit group wins.
    """
    if not token:
        return None
    match = re.search(r"\d{4}", token) or re.search(r"\d{3}", token)
    return match.group(0) if match else None


def fold(name: str) -> str:
    """*name* reduced to a comparison key: diacritics dropped, words only.

    Deliberately local rather than :func:`composer_models.normalize.dedup_key` —
    that function seeds entity uuids and must not shift underneath them, and this
    tier cannot import it. Used only to decide that two spellings on one page are
    the same person.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c)).lower()
    return " ".join(_WORD_RE.findall(plain))


@dataclass(frozen=True)
class Track:
    """One line of the tracklist: a movement, or a piece short enough to stand alone."""

    title: str
    duration: str | None = None

    def as_raw(self) -> dict[str, str | None]:
        return {"title": self.title, "duration": self.duration}


@dataclass(frozen=True)
class WorkEntry:
    """One work on the release, with the tracks listed under it."""

    title: str
    tracks: tuple[Track, ...] = ()
    subtitle: str | None = None


@dataclass
class ComposerBlock:
    """The works attributed to one composer, in listing order.

    ``name`` is the heading verbatim (or None for tracks listed before any
    heading); ``composer`` is what may be claimed from it.
    """

    name: str | None = None
    born: str | None = None
    died: str | None = None
    works: list[WorkEntry] = field(default_factory=list)
    #: Whether the last work was opened by a heading. A track goes *under* a
    #: heading, but a run of bare bullets is a list of separate pieces — without
    #: this, a recital of 24 Dowland lute pieces loads as one 24-movement work.
    under_heading: bool = False

    @property
    def composer(self) -> str | None:
        """The composer to attribute these works to, or None.

        None for a placeholder attribution (see ``_NOT_A_COMPOSER``): a work
        mention carrying one would mint it as a person entity downstream.
        """
        if self.name is None or fold(self.name) in _NOT_A_COMPOSER:
            return None
        return self.name


@dataclass(frozen=True)
class _Line:
    """One classified line of the blob."""

    kind: str  # "composer" | "track" | "work"
    body: str
    duration: str | None = None
    born: str | None = None
    died: str | None = None


def _strip_duration(body: str) -> tuple[str, str | None]:
    match = _DURATION_RE.search(body)
    if match is None:
        return body, None
    return body[: match.start()].strip(), f"{match.group(1)}'{match.group(2)}"


def _classify(raw_line: str, known: dict[str, str]) -> _Line:
    """One line, as the thing it is.

    *known* maps a folded composer name to its preferred spelling — the page's
    own Composers credits, plus the headings seen so far. It is what lets a
    repeated composer be recognised the second time, when the editors write the
    name alone without repeating the dates.
    """
    stripped = _BULLET_RE.sub("", raw_line, count=1)
    bulleted = stripped != raw_line
    body, duration = _strip_duration(stripped)
    match = _LIFE_RE.match(body)
    if match is not None and (match.group("born") or match.group("died")):
        name = match.group("name").strip(" ,;:-\u2013\u2014")
        return _Line("composer", name, born=_year(match.group("born")), died=_year(match.group("died")))
    single = _SINGLE_DATE_RE.match(body)
    if single is not None:
        year = _year(single.group("year"))
        born = year if single.group("mark").lower() == "b" else None
        return _Line("composer", single.group("name").strip(), born=born, died=None if born else year)
    if body and fold(body) in known:
        return _Line("composer", known[fold(body)])
    if bulleted:
        return _Line("track", body, duration=duration)
    if _NOTE_RE.match(body) or _CARRIER_RE.match(body):
        return _Line("note", body)
    return _Line("work", body)


def _add_track(block: ComposerBlock, track: Track) -> None:
    """Attach a track to the work it belongs under.

    With a work heading above it the track is a movement of that work. With
    none — a recital of separate short pieces, which is how the early-music and
    lute albums are listed — each track *is* a work.
    """
    if block.works and block.under_heading:
        last = block.works[-1]
        block.works[-1] = WorkEntry(last.title, (*last.tracks, track), last.subtitle)
        return
    block.works.append(WorkEntry(track.title, (track,)))


def _add_work(block: ComposerBlock, title: str) -> None:
    """Open a work, or continue the one above.

    The editors routinely break one work heading over two lines — the name on
    one, the key and catalogue number on the next (``Sonata in D major`` /
    ``Re majeur / D-dur D.384, Op.137 no.1``), or the scoring and date
    (``Les Illuminations op. 18`` / ``for high voice and string orchestra
    (1939)``). Read literally that is two works: the first with no tracks, the
    second titled after a key signature — and both would be minted as canonical
    works downstream.

    A heading arriving while the work above it has collected no tracks is that
    second line. The first keeps the title, because it is the one that names the
    work; the continuation is kept as a subtitle rather than dropped.
    """
    if block.works and not block.works[-1].tracks:
        last = block.works[-1]
        subtitle = f"{last.subtitle} {title}" if last.subtitle else title
        block.works[-1] = WorkEntry(last.title, last.tracks, subtitle)
        block.under_heading = True
        return
    block.works.append(WorkEntry(title))
    block.under_heading = True


@dataclass
class _TitleEcho:
    """The release title, matched off the front of the blob as the editors echo it.

    Matched as a *token prefix* rather than the whole string, and across as many
    lines as it takes: the page titled "Assassin's Creed: The Piano Collection"
    opens with ``ASSASSIN'S CREED`` on one line and ``THE PIANO COLLECTION`` on
    the next, and ``Corde di luna. Romantic songs and canzonette`` opens with
    ``Corde di Luna`` then ``Romantic Songs and canzonette``. Read as works those
    mint canonical works named after the album.

    Only consumed from the very start of the blob, which is the only place the
    echo appears — without that a release named after the work it opens with
    would lose that work.
    """

    tokens: list[str]
    consumed: int = 0

    def consumes(self, title: str) -> bool:
        """Whether *title* is the next stretch of the echoed release title."""
        if self.consumed >= len(self.tokens):
            return False
        words = fold(title).split()
        if not words or self.tokens[self.consumed : self.consumed + len(words)] != words:
            return False
        self.consumed += len(words)
        return True

    def close(self) -> None:
        """Stop matching: the blob has moved on to its contents."""
        self.consumed = len(self.tokens)


def _open_block(
    blocks: list[ComposerBlock], current: ComposerBlock, line: _Line, known: dict[str, str]
) -> ComposerBlock:
    """Close *current* if it holds anything, and return the block *line* opens."""
    name = known.get(fold(line.body), line.body)
    known.setdefault(fold(name), name)
    if current.name is not None or current.works:
        blocks.append(current)
    return ComposerBlock(name=name, born=line.born, died=line.died)


def parse_contents(
    fragment: str, credited: Iterable[str] = (), album_title: str | None = None
) -> tuple[ComposerBlock, ...]:
    """The blob as composer blocks, in listing order.

    *credited* is the album's Composers-column credits. They are the site's own
    canonical spellings, so a heading that matches one is re-spelled from it —
    the headings are styled (``CLAUDE DEBUSSY``) and the credits are not
    (``Claude Debussy``), and the mention's composer should read as a name.

    *album_title* is dropped where it appears as a heading: the editors often
    open the blob by repeating the release title, and read as a work it becomes
    a composer-less mention that mints a canonical work named after the album.
    Dropping it is safe even on a single-work release — the caller falls back to
    the album title when the blob yields no works at all, so the same mention
    comes out either way.

    Lines before the first composer heading go into a leading block with no
    name; a blob with no heading at all yields exactly that one block, so its
    works are still reported, just unattributed.
    """
    known = {fold(name): name for name in credited if name}
    echo = _TitleEcho(fold(album_title).split() if album_title else [])
    blocks: list[ComposerBlock] = []
    current = ComposerBlock()
    for raw_line in lines(fragment):
        line = _classify(raw_line, known)
        if line.kind == "composer":
            echo.close()
            current = _open_block(blocks, current, line, known)
        elif line.kind == "track":
            echo.close()
            _add_track(current, Track(line.body, line.duration))
        elif line.kind == "work" and line.body and not echo.consumes(line.body):
            echo.close()
            _add_work(current, line.body)
    if current.name is not None or current.works:
        blocks.append(current)
    return tuple(blocks)
