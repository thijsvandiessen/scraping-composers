"""One concert's detail page: the roles the listing does not carry.

The archive's result fragments give a concert's whole programme in data
attributes (see :mod:`.performances`), but they give its performers as one flat,
unlabelled list — only the conductor is credited, and only when the site renders
a ``CONDUCTOR``-headed block. The detail page labels every credit:

    <div class="grid-x grid-padding-x align-center programm-info event">
      <div class="entry"><span class="subhead">Conductor</span>
                         <span class="subline primary-color">Otto Nicolai</span></div>
      <div class="entry"><span class="subhead">Soprano</span>
                         <span class="subline primary-color">Jenny Lutzer</span></div>
      <div class="entry"><span class="subhead">Program</span>
        <span class="subline primary-color">Wolfgang Amadeus Mozart</span>
        <span class="subline primary-color cast-programm"><em>Symphony [No. 40] ...</em></span>
        <span class="subline primary-color cast-programm pause">-- INTERMISSION --</span>
      </div>
    </div>

That is the whole point of fetching it: the instrument and voice types, and the
occasional conductor the listing drops (concert 9546 credits "Musikalische
Leitung" and the fragment credits nobody). The programme comes along for free
and in a better shape — composer and work alternate as siblings, so the
``;``-splitting and vocabulary lookup :mod:`.performances` needs to realign the
data attributes is not needed here, and an intermission is marked by a class
rather than inferred from its text.

**Everything is read from inside that one block.** A detail page also carries an
"Other dates" section built from ``<div class="event-module">`` elements — the
same markup the result fragments use, but with a reduced attribute set — so a
parse scoped to the page rather than the block would quietly pick up the
neighbouring concerts' credits as if they were this concert's.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from composer_schema.kinds import looks_like_ensemble

# The one credits/programme block. Bounded by the "Other dates" markup that may
# follow it, which is why the block is located rather than the page scanned.
_BLOCK = re.compile(
    r'<div class="[^"]*\bprogramm-info\b[^"]*">(.*?)(?=<div class="grid-container|<footer|\Z)',
    re.DOTALL,
)
_ENTRY = re.compile(r'<div class="entry">(.*?)</div>', re.DOTALL)
_SUBHEAD = re.compile(r'<span class="subhead">(.*?)</span>', re.DOTALL)
_SUBLINE = re.compile(r'<span class="(subline[^"]*)">(.*?)</span>', re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

#: The label the programme entry carries, in place of a credit's role.
_PROGRAM = "program"

#: Marks a programme span as a work title rather than its composer, and (with
#: ``pause``) an intermission rather than a work.
_WORK_CLASS = "cast-programm"
_PAUSE_CLASS = "pause"

# Credit labels that name the whole orchestra or choir rather than a musician.
# Only the labels: a named ensemble is caught by looks_like_ensemble instead,
# which is what recognises "Singverein der Gesellschaft der Musikfreunde in
# Wien" credited under no label at all.
_ENSEMBLE_LABELS = frozenset({"orchestra", "orchester", "chorus", "choir", "chor", "chorus master"})

# Labels that mean the credit conducted. The English pages carry German labels
# too, and combined ones ("Conductor and Piano Soloist"), which is why this
# matches within the label rather than against it.
_CONDUCTOR = re.compile(r"conduct(?:or|ing)?|dirigentin|dirigent|musikalische leitung|leitung", re.IGNORECASE)
# What is left of a label once the conducting words and the grammar joining them
# to the rest are gone. "Conductor" leaves nothing; "Conductor and Piano
# Soloist" leaves "Piano Soloist", so that credit played as well as conducted.
_JOINERS = re.compile(r"\b(?:and|und|soloist|solist|solistin)\b|[^\w\s]", re.IGNORECASE)


@dataclass(frozen=True)
class ConcertDetail:
    """One concert as its own page states it.

    ``credits`` keeps every ``(label, name)`` verbatim, in page order, because
    the label vocabulary is the site's and not ours — it spans two languages and
    the odd typo ("Saxophon"), so it is recorded rather than mapped. The three
    tuples after it are the reading of those credits the warehouse wants.
    """

    credits: tuple[tuple[str, str], ...]
    conductors: tuple[str, ...]
    soloists: tuple[tuple[str, str | None], ...]  # (name, discipline)
    ensembles: tuple[str, ...]
    programme: tuple[tuple[str | None, str], ...]  # (composer, work title)


def _text(markup: str) -> str:
    return _WS.sub(" ", html.unescape(_TAGS.sub(" ", markup))).strip()


def _entries(block: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """Each ``entry`` of the block as its label and its ``(class, text)`` spans."""
    found: list[tuple[str, list[tuple[str, str]]]] = []
    for entry in _ENTRY.findall(block):
        label = _SUBHEAD.search(entry)
        spans = [(classes, _text(value)) for classes, value in _SUBLINE.findall(entry)]
        found.append((_text(label.group(1)) if label else "", spans))
    return found


def _programme(spans: list[tuple[str, str]]) -> list[tuple[str | None, str]]:
    """Pair each work of the programme entry with the composer named above it.

    A work span with no composer span before it continues the previous work's
    composer, the same rule an empty composer slot follows in the fragments.
    """
    paired: list[tuple[str | None, str]] = []
    composer: str | None = None
    for classes, value in spans:
        if _WORK_CLASS not in classes:
            composer = value or composer
        elif _PAUSE_CLASS not in classes and value:
            paired.append((composer, value))
    return paired


def _credits(entries: list[tuple[str, list[tuple[str, str]]]]) -> list[tuple[str, str]]:
    """Every ``(label, name)`` credited, the programme entry excluded."""
    return [
        (label, name) for label, spans in entries if label.lower() != _PROGRAM for _, name in spans if name
    ]


def _unique(names: list[str]) -> tuple[str, ...]:
    """*names* in first-seen order, without repeats.

    A page may credit the same name twice — concert 8056 lists Daniel Barenboim
    under "Conductor" and again under "Conductor and Piano Soloist".
    """
    return tuple(dict.fromkeys(names))


def _split(credits: list[tuple[str, str]]) -> tuple[list[str], list[tuple[str, str | None]], list[str]]:
    """Sort the credits into conductors, soloists with their discipline, and ensembles.

    A conductor who also played keeps the combined label as a discipline too —
    "Conductor and Piano Soloist" is one credit that asserts both.
    """
    conductors: list[str] = []
    soloists: list[tuple[str, str | None]] = []
    ensembles: list[str] = []
    for label, name in credits:
        if label.lower() in _ENSEMBLE_LABELS or looks_like_ensemble(name):
            ensembles.append(name)
            continue
        if not _CONDUCTOR.search(label):
            soloists.append((name, label or None))
            continue
        conductors.append(name)
        if _JOINERS.sub("", _CONDUCTOR.sub("", label)).strip():
            # the label names an instrument as well: this credit played too
            soloists.append((name, label))
    return conductors, soloists, ensembles


def detail(page: str) -> ConcertDetail | None:
    """Read one concert detail page, or None when it carries no credits block."""
    block = _BLOCK.search(page)
    if block is None:
        return None
    entries = _entries(block.group(1))
    credits = _credits(entries)
    conductors, soloists, ensembles = _split(credits)
    programme: list[tuple[str | None, str]] = []
    for label, spans in entries:
        if label.lower() == _PROGRAM:
            programme = _programme(spans)
            break
    return ConcertDetail(
        credits=tuple(credits),
        conductors=_unique(conductors),
        soloists=tuple(dict.fromkeys(soloists)),
        ensembles=_unique(ensembles),
        programme=tuple(programme),
    )
