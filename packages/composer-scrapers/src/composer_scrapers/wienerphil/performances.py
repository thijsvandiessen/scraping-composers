"""The archive result fragments: one work mention per programme item.

Each concert is a ``<div class="event-module">`` that carries its whole
programme in data attributes, so no detail page has to be fetched:

    <div class="event-module" data-title="Philharmonic Concert"
         data-composers="Ludwig van Beethoven;Luigi Cherubini;"
         data-works="Symphony No. 7 in A Major, op. 92;Arie aus &quot;Fanisca&quot;;"
         data-performers="Otto Nicolai;" data-location="Hofburg Palace, Vienna, Austria"
         data-date="1842-03-28">
      <h2><a href="/en/konzerte/philharmonic-concert/2465/">Philharmonic Concert</a></h2>
      <div class="cell h">12:30</div>
      <div class="cell medium-9 event-area">Hofburg Palace, Redoutensaal, Vienna, Austria</div>
      <div class="c cell small-6"><h3>CONDUCTOR</h3><p>Otto Nicolai</p></div>

``data-composers`` and ``data-works`` are positionally aligned — slot *n* of one
names the composer of slot *n* of the other — which is the whole reason composer
attribution works without the detail pages. Three things complicate the split,
all handled by :func:`programme`:

* a work title may contain a ``;`` itself, over-splitting the field (37 of the
  archive's 5456 catalogued titles do), so the ``werk`` filter vocabulary is
  used to rejoin the fragments;
* ``-- INTERMISSION --`` appears as a work slot with *no* composer slot opposite
  it, which shifts every work after it onto the wrong composer;
* an empty composer slot means "same composer as the previous work" rather than
  "unknown", so it carries forward.

Together those bring every one of the archive's concerts into alignment. A
concert that still does not line up is emitted with no composer attribution at
all rather than a guessed one — a wrong composer is worse than a missing one.

The performer field is one flat list with no roles: only the conductor is
labelled, in the credit block, and only when the site renders one. Soloists are
therefore whatever is left once the conductors and the ensembles are taken out,
and carry no discipline. That is what :mod:`.details` is for: :func:`merge`
folds a concert's own page back over this reading, which is where the instrument
and voice types come from and where a conductor the credit block omitted
reappears. A concert whose detail page could not be fetched keeps this reading
unchanged.

One quirk of the field is worth naming because it reads as a performer: the site
renders a missing performer as the literal string ``None`` (873 concerts carry
one), which is dropped rather than credited to a musician of that name.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Container, Iterator
from dataclasses import dataclass, replace

from composer_schema.kinds import looks_like_ensemble

from .. import SourceWorkMention
from .details import ConcertDetail
from .fetch import BASE_URL

log = logging.getLogger(__name__)

# One concert. The blocks are siblings, so each runs up to the next opener.
_MODULE = re.compile(r'<div class="event-module"(.*?)(?=<div class="event-module"|\Z)', re.DOTALL)
_ATTR = re.compile(r'\bdata-(title|composers|works|performers|location|date)="([^"]*)"')
# <h2><a href="/en/konzerte/philharmonic-concert/2465/">
_LINK = re.compile(r'<h2><a href="([^"]+)"')
_CONCERT_ID = re.compile(r"/(\d+)/?$")
_TIME = re.compile(r'<div class="cell h">([^<]*)</div>')
# <p class="st" role="doc-subtitle">Salzburg Festival 2026</p> — the series or
# festival a concert belongs to, where it belongs to one.
_SUBTITLE = re.compile(r'<p class="st" role="doc-subtitle">([^<]*)</p>')
_VENUE = re.compile(r'<div class="cell medium-9 event-area">([^<]*)</div>')
# the sole credit block: <div class="c cell small-6"><h3>CONDUCTOR</h3><p>Otto Nicolai</p>
_CREDIT = re.compile(r'<div class="c cell[^"]*"><h3>([^<]*)</h3><p>(.*?)</p>', re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
# "-- INTERMISSION --". Placed among the works, credited to nobody.
_INTERMISSION = re.compile(r"^\s*-{2,}\s*(?:INTERMISSION|PAUSE)\s*-{2,}\s*$", re.IGNORECASE)

#: Most fragments any one ';'-bearing title in the archive splits into.
_MAX_TITLE_FRAGMENTS = 4

#: How the site renders a performer slot it has no performer for.
_MISSING = "None"


@dataclass(frozen=True)
class Concert:
    """One concert of the archive, with its programme already paired up."""

    concert_id: str
    url: str
    title: str
    subtitle: str | None
    date: str
    time: str | None
    venue: str | None
    location: str | None
    conductors: tuple[str, ...]
    conductor_labels: tuple[str, ...]
    soloists: tuple[tuple[str, str | None], ...]  # (name, discipline)
    ensembles: tuple[str, ...]
    programme: tuple[tuple[str | None, str], ...]  # (composer, work title)
    #: Every ``(label, name)`` the concert's own page credited, verbatim; empty
    #: until :func:`merge` has folded that page in.
    credits: tuple[tuple[str, str], ...] = ()


def _text(markup: str) -> str:
    return _WS.sub(" ", html.unescape(_TAGS.sub(" ", markup))).strip()


def _find(pattern: re.Pattern[str], body: str) -> str | None:
    match = pattern.search(body)
    return (_text(match.group(1)) or None) if match else None


def _slots(value: str) -> list[str]:
    """Split a ``;``-terminated attribute into its slots.

    Only the single trailing empty element is dropped, because that one is the
    terminator. An *interior* blank is a real slot the archive left empty, and
    discarding it would shift every following work onto the wrong composer.
    """
    if not value:
        return []
    parts = value.split(";")
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def _work_slots(value: str, titles: Container[str]) -> list[str]:
    """Split ``data-works``, rejoining titles that contain a ``;`` themselves.

    ``titles`` is the archive's own ``werk`` filter vocabulary: a run of
    fragments that rejoins into a catalogued title was one title all along. The
    longest such run wins, so a title that is a prefix of a longer one does not
    end the join early.
    """
    parts = _slots(value)
    slots: list[str] = []
    index = 0
    while index < len(parts):
        joined: int | None = None
        for end in range(index + 2, min(index + _MAX_TITLE_FRAGMENTS, len(parts)) + 1):
            if ";".join(parts[index:end]) in titles:
                joined = end
        if joined is None:
            slots.append(parts[index])
            index += 1
        else:
            slots.append(";".join(parts[index:joined]))
            index = joined
    return slots


def _fold_continuations(works: list[str]) -> list[str]:
    """Fold leading-whitespace fragments into the slot before them.

    The backstop for a ``;``-bearing title the ``werk`` vocabulary does not
    carry — it lists the German title where a fragment renders the English one.
    Such a fragment always opens with the space that followed the semicolon.
    """
    folded = list(works)
    while True:
        index = next((i for i in range(1, len(folded)) if folded[i][:1].isspace()), None)
        if index is None:
            return folded
        folded[index - 1] = f"{folded[index - 1]};{folded[index]}"
        del folded[index]


def programme(composers: str, works: str, titles: Container[str]) -> list[tuple[str | None, str]]:
    """Pair each work of a concert with its composer.

    Returns ``(composer, title)`` in programme order. A composer is ``None``
    where the archive names none, and where the programme cannot be lined up at
    all — attributing a work to the wrong composer is worse than to nobody.
    """
    named = _slots(composers)
    pieces = [work for work in _work_slots(works, titles) if not _INTERMISSION.match(work)]
    if len(pieces) > len(named):
        pieces = _fold_continuations(pieces)
    if len(pieces) != len(named):
        log.warning("programme does not line up: %d composers, %d works", len(named), len(pieces))
        return [(None, piece.strip()) for piece in pieces]

    paired: list[tuple[str | None, str]] = []
    composer: str | None = None
    for name, piece in zip(named, pieces, strict=True):
        # an empty slot continues the previous work's composer
        composer = name.strip() or composer
        paired.append((composer, piece.strip()))
    return paired


def _credits(body: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The conductors of a concert, and the labels they were credited under.

    Every credit block in the archive names a conductor, but not always under
    that word: the English pages carry German labels ("DIRIGENTIN") and combined
    ones ("CONDUCTOR AND PIANO SOLOIST") too, so the label is kept verbatim in
    the payload rather than used to decide the role.
    """
    names: list[str] = []
    labels: list[str] = []
    for label, name in _CREDIT.findall(body):
        credited = _text(name)
        if credited:
            names.append(credited)
            labels.append(_text(label))
    return tuple(names), tuple(labels)


def _concert(body: str, titles: Container[str]) -> Concert | None:
    attributes = {key: html.unescape(value) for key, value in _ATTR.findall(body)}
    link = _LINK.search(body)
    date = attributes.get("date")
    if link is None or not date:
        return None
    href = html.unescape(link.group(1))
    identifier = _CONCERT_ID.search(href)
    if identifier is None:
        return None

    conductors, labels = _credits(body)
    performers = [
        name.strip()
        for name in _slots(attributes.get("performers", ""))
        if name.strip() and name.strip() != _MISSING
    ]
    ensembles = tuple(name for name in performers if looks_like_ensemble(name))
    credited = set(conductors) | set(ensembles)
    return Concert(
        concert_id=identifier.group(1),
        url=BASE_URL + href if href.startswith("/") else href,
        title=attributes.get("title", "").strip(),
        subtitle=_find(_SUBTITLE, body),
        date=date.strip(),
        time=_find(_TIME, body),
        venue=_find(_VENUE, body),
        location=attributes.get("location", "").strip() or None,
        conductors=conductors,
        conductor_labels=labels,
        soloists=tuple((name, None) for name in performers if name not in credited),
        ensembles=ensembles,
        programme=tuple(programme(attributes.get("composers", ""), attributes.get("works", ""), titles)),
    )


def merge(concert: Concert, detail: ConcertDetail) -> Concert:
    """*concert* as its own detail page corrects and completes it.

    The detail page wins wherever the two disagree, because it is the fuller
    record: it labels every credit where the fragment labels at most the
    conductor, and it pairs each work with its composer as siblings in the
    markup, where the fragment leaves that to be reconstructed from two
    positionally aligned attributes.

    Nothing the fragment knew is dropped, though. A performer the detail page
    does not name keeps its place, with no discipline — the two never disagreed
    on *who* played in any concert sampled, but losing a name to a markup change
    would be silent, and an undisciplined soloist is what the source gave us
    before this page was read anyway.
    """
    named = {name for name, _ in detail.soloists} | set(detail.conductors) | set(detail.ensembles)
    return replace(
        concert,
        conductors=tuple(dict.fromkeys(concert.conductors + detail.conductors)),
        soloists=tuple(
            dict.fromkeys(
                detail.soloists + tuple((name, None) for name, _ in concert.soloists if name not in named)
            )
        ),
        ensembles=tuple(dict.fromkeys(concert.ensembles + detail.ensembles)),
        programme=detail.programme or concert.programme,
        credits=detail.credits,
    )


def concerts(fragment: str, titles: Container[str]) -> Iterator[Concert]:
    """Yield every concert of one result fragment."""
    for match in _MODULE.finditer(fragment):
        concert = _concert(match.group(1), titles)
        if concert is not None:
            yield concert


def mentions(concert: Concert) -> Iterator[SourceWorkMention]:
    """Yield one work mention per programme item, carrying the concert context.

    The concert-level fields are repeated on every work because that is what the
    warehouse groups on: :mod:`composer_warehouse.concerts.derive` rebuilds a
    concert from the payloads of its mentions.
    """
    for index, (composer, title) in enumerate(concert.programme):
        yield SourceWorkMention(
            external_id=f"perf:{concert.concert_id}:{index}",
            title=title,
            composer=composer,
            raw={
                "concert_id": concert.concert_id,
                "url": concert.url,
                "index": index,
                "title": concert.title,
                "subtitle": concert.subtitle,
                "date": concert.date,
                "time": concert.time,
                "venue": concert.venue,
                "location": concert.location,
                "composer": composer,
                "work": title,
                "conductors": list(concert.conductors),
                "conductor_labels": list(concert.conductor_labels),
                # a discipline is None for a soloist the concert's own page did
                # not name; the result listing labels nobody
                "soloists": [
                    {"name": name, "discipline": discipline} for name, discipline in concert.soloists
                ],
                "ensembles": list(concert.ensembles),
                # every credit of the detail page, verbatim: the label vocabulary
                # is the site's, spans two languages, and is recorded, not mapped
                "credits": [list(credit) for credit in concert.credits],
            },
        )
