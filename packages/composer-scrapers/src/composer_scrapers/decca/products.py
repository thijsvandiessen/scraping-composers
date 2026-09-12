"""One product family's GraphQL payload, read as recordings, works and credits.

The shape the API answers with is
``ProductFamily -> Product -> Carrier -> Track``, where a *family* is the
release as the catalogue markets it, a *product* one physical or digital
edition of it (CD, vinyl, download), a *carrier* a disc within that edition, and
a *track* the leaf. This module flattens that into one :class:`Recording` per
product, because a product is what carries the SKU — the identity that
``recordings/cluster.py`` refuses to merge across.

Each track names its works as a ranked hierarchy: ``level: 1`` is the work,
2-5 its acts, scenes and movements. Grouping an album's tracks by their level-1
work is what turns a 26-track *Un ballo in maschera* into one work mention
instead of 26 near-duplicates.

Composer attribution is the reason this source is worth reading structurally at
all, and it needs care because the obvious field is a trap. ``Artist.isComposer``
is a *curation* flag, not a fact: it is ``false`` for John Williams on a
film-music album and ``false`` for all 80 artists on Decca's own roster, so
filtering on it drops attribution from 87% of tracks to 63%. What is reliable is
``TrackContributor.function``. The order used here, and why:

1. Contributors whose function names them a composer. That is the credit the
   label itself files the track under.
2. When more than one comes back, prefer the ones ``isComposer`` *does* flag.
   This is what separates Verdi (flagged) from his librettist Antonio Somma
   (not flagged), who the catalogue also files under ``function: "Composer"``.
   If the preference empties the set the whole set is kept — a genuine
   two-composer track must not become no composers.
3. Failing any composer-function credit at all, the ``COMPOSER``-typed track
   heading, which is a display string rather than a relation but covers 90% of
   tracks on its own.

Every candidate and its evidence survives into ``raw`` either way. Nothing here
picks a winner between two equally credited people, and no name is promoted to
composer because it looked like one.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from composer_schema import resolve_entity_kind

from .urls import product_url

#: Not people. The catalogue uses these where a composer is unknown, and they
#: would otherwise become entities that every anonymous work in the corpus
#: resolves to.
_PLACEHOLDERS = frozenset({"anonymous", "anonymus", "traditional", "unknown", "n.n.", "various"})

#: Credits that describe how the recording was *made*. Kept in ``raw`` for later
#: passes, never emitted as performers.
_PRODUCTION = (
    "producer",
    "engineer",
    "editor",
    "mastering",
    "mixing",
    "mixer",
    "director",
    "photograph",
    "design",
    "liner",
    "notes",
    "translat",
    "supervisor",
    "coordinator",
    "assistant",
    "asst",
    "technician",
    "consultant",
    "restoration",
    "remaster",
    "artwork",
    "booklet",
)

#: Credits for authoring the *text* rather than performing it. Librettists,
#: lyricists and arrangers are real contributors but they are not performers,
#: and folding them into the participant list would credit them as such.
_AUTHORING = frozenset(
    {"author", "librettist", "lyricist", "arranger", "work arranger", "transcription", "adaptation"}
)

#: Prepares a chorus, does not perform. Named explicitly because the keyword
#: match below would otherwise read "Chorus Master" as an ensemble.
_NON_PERFORMING = frozenset({"chorus master", "choir master", "chorus mistress", "repetiteur"})

_ENSEMBLE_FUNCTIONS = ("orchestra", "choir", "chorus", "ensemble", "band")

_LIFE_DATES_RE = re.compile(r"^(?P<name>.*?)\s*\((?P<born>\d{4})?\s*-{1,2}\s*(?P<died>\d{4})?\s*\)$")


@dataclass(frozen=True)
class Credit:
    """One artist's credit on a track, as the catalogue files it."""

    artist_id: int
    name: str
    function: str
    flagged_composer: bool = False
    url_alias: str | None = None

    @property
    def kind(self) -> str:
        return resolve_entity_kind("person", self.name)

    @property
    def role(self) -> str:
        """The participant role, in the vocabulary ``recordings/derive`` admits.

        Ensemble-ness is decided by the *name*, not the function: the shared
        ``resolve_entity_kind`` already knows "Wiener Philharmoniker" and
        "Accademia Bizantina" are ensembles, and it stays consistent with the
        entity kind the same credit is emitted under.
        """
        if self.kind == "ensemble":
            return "ensemble"
        function = self.function.casefold()
        if function == "conductor":
            return "conductor"
        if any(word in function for word in _ENSEMBLE_FUNCTIONS):
            return "ensemble"
        return "soloist"


@dataclass(frozen=True)
class Track:
    """A track, with the composers resolved for it."""

    number: int | None
    title: str
    carrier: str | None = None
    isrc: str | None = None
    duration: int | None = None
    location: str | None = None
    recorded_on: str | None = None
    composers: tuple[str, ...] = ()
    from_heading: bool = False

    def as_raw(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "carrier": self.carrier,
            "isrc": self.isrc,
            "duration": self.duration,
            "recording_location": self.location,
            "recorded_on": self.recorded_on,
            "composers": list(self.composers),
        }


@dataclass(frozen=True)
class WorkMention:
    """A level-1 work as it appears on one product."""

    work_id: int
    title: str
    composer: str | None
    composers: tuple[str, ...]
    tracks: tuple[Track, ...]


@dataclass(frozen=True)
class Recording:
    """One product: the release a SKU identifies, and everything on it."""

    product_id: int
    family_id: int
    slug: str
    title: str
    release_date: str | None = None
    label: str | None = None
    catalogue_number: str | None = None
    format: str | None = None
    category: str | None = None
    mentions: tuple[WorkMention, ...] = ()
    participants: tuple[Credit, ...] = ()
    composers: tuple[Credit, ...] = ()
    production: tuple[Credit, ...] = ()
    life_dates: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)

    @property
    def url(self) -> str:
        return product_url(self.slug)

    @property
    def record_key(self) -> str:
        """The identity ``recordings/derive`` folds this product's mentions into.

        One key per product, so an album's works arrive as one recording rather
        than one per work.
        """
        return f"product:{self.product_id}"


def _text(value: Any) -> str | None:
    """A non-empty trimmed string, or None — the API returns both '' and null."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _placeholder(name: str) -> bool:
    return name.casefold().strip(" .") in _PLACEHOLDERS


def split_heading(heading: str) -> list[tuple[str, str | None, str | None]]:
    """A ``COMPOSER`` heading as ``(name, born, died)`` triples.

    Two encodings have to come apart. A slash separates transliterations of the
    same list ("… Joachim A.C. Zarnack (--)/Ernest Anschutz, Joachim August
    Zarnack"), so only the first variant is read — the others are the same
    people spelled differently. Commas separate distinct people, usually the
    composer followed by a librettist. Life dates ride along in parentheses
    ("Benjamin Britten (1913 - 1976)", "André Previn (1929 - )") and are worth
    keeping: they are the only birth and death years this source states.
    """
    people: list[tuple[str, str | None, str | None]] = []
    for part in heading.split("/", 1)[0].split(","):
        name = part.strip()
        if not name:
            continue
        match = _LIFE_DATES_RE.match(name)
        if match is None:
            people.append((name, None, None))
            continue
        stripped = match.group("name").strip()
        if stripped:
            people.append((stripped, match.group("born"), match.group("died")))
    return people


def _track_composers(track: dict[str, Any]) -> tuple[list[Credit], bool]:
    """The composer credits for one track, and whether they came from a heading.

    See the module docstring for why ``function`` leads and ``isComposer`` only
    breaks ties.
    """
    credits = _credits(track)
    candidates = [
        credit
        for credit in credits
        if "composer" in credit.function.casefold() and not _placeholder(credit.name)
    ]
    if candidates:
        if len(candidates) > 1:
            flagged = [credit for credit in candidates if credit.flagged_composer]
            if flagged:
                candidates = flagged
        return _unique(candidates), False
    return _heading_composers(track, credits), True


def _heading_composers(track: dict[str, Any], credits: list[Credit]) -> list[Credit]:
    """Composers named only by the display heading.

    These carry no artist id — the heading is a string, not a relation — so they
    are keyed by name downstream. All the names it lists are kept: the catalogue
    usually leads with the composer, but not always, which is exactly why the
    alternatives ride along rather than being discarded.

    A name that is also a *performing* credit on the same track is dropped. On a
    track the label filed under nobody as composer, a heading echoing the
    performer is the heading describing who is on the record, not who wrote it —
    a spoken-word track like "What the Edinburgh Festival Has Meant To Me" lists
    Kathleen Ferrier, its speaker, under ``COMPOSER``. Measured over a 70-family
    sample this drops nothing that was a real composer credit.
    """
    performing = {
        credit.name.casefold()
        for credit in credits
        if not _is_authoring(credit.function) and not _is_production(credit.function)
    }
    for heading in track.get("headings") or []:
        if not isinstance(heading, dict) or heading.get("type") != "COMPOSER":
            continue
        name = _text(heading.get("name"))
        if name is None:
            continue
        return [
            Credit(artist_id=0, name=person, function="Composer")
            for person, _born, _died in split_heading(name)
            if not _placeholder(person) and person.casefold() not in performing
        ]
    return []


def heading_life_dates(track: dict[str, Any]) -> dict[str, tuple[str | None, str | None]]:
    """Birth and death years the track's ``COMPOSER`` heading states, by name."""
    dates: dict[str, tuple[str | None, str | None]] = {}
    for heading in track.get("headings") or []:
        if not isinstance(heading, dict) or heading.get("type") != "COMPOSER":
            continue
        name = _text(heading.get("name"))
        if name is None:
            continue
        for person, born, died in split_heading(name):
            if born or died:
                dates[person] = (born, died)
    return dates


def _credits(track: dict[str, Any]) -> list[Credit]:
    """Every contributor on a track that names an artist and a function."""
    found: list[Credit] = []
    for entry in track.get("contributors") or []:
        if not isinstance(entry, dict):
            continue
        artist = entry.get("artist")
        if not isinstance(artist, dict):
            continue
        name = _text(artist.get("screenname"))
        artist_id = artist.get("idRaw")
        if name is None or not isinstance(artist_id, int):
            continue
        found.append(
            Credit(
                artist_id=artist_id,
                name=name,
                function=_text(entry.get("function")) or "",
                flagged_composer=bool(artist.get("isComposer")),
                url_alias=_text(artist.get("urlAlias")),
            )
        )
    return found


def _unique(credits: list[Credit]) -> list[Credit]:
    """Credits deduplicated by artist, keeping the first spelling seen."""
    seen: dict[tuple[int, str], Credit] = {}
    for credit in credits:
        seen.setdefault((credit.artist_id, credit.name.casefold()), credit)
    return list(seen.values())


def _is_production(function: str) -> bool:
    folded = function.casefold()
    return any(word in folded for word in _PRODUCTION)


def _is_authoring(function: str) -> bool:
    folded = function.casefold()
    return folded in _AUTHORING or "composer" in folded


def _recorded_on(track: dict[str, Any]) -> str | None:
    """The recording date as far as it is stated: ``YYYY``, ``YYYY-MM`` or full ISO.

    Partial dates stay partial rather than being padded to January the 1st — a
    guessed day would be indistinguishable from a stated one downstream.
    """
    year = track.get("recordingDateYear")
    if not isinstance(year, int):
        return None
    month = track.get("recordingDateMonth")
    if not isinstance(month, int):
        return f"{year:04d}"
    day = track.get("recordingDateDay")
    if not isinstance(day, int):
        return f"{year:04d}-{month:02d}"
    return f"{year:04d}-{month:02d}-{day:02d}"


def _level_one_work(track: dict[str, Any]) -> tuple[int, str] | None:
    """The track's top-level work as ``(id, title)``.

    Level 1 is the work; deeper levels are its acts and movements, which is the
    grouping that keeps a whole opera as one mention.
    """
    for edge in (track.get("works") or {}).get("edges") or []:
        node = edge.get("node") if isinstance(edge, dict) else None
        if not isinstance(node, dict) or node.get("level") != 1:
            continue
        work_id = node.get("idRaw")
        title = _text(node.get("name"))
        if isinstance(work_id, int) and title is not None:
            return work_id, title
    return None


def recordings(family: dict[str, Any], slug: str) -> Iterator[Recording]:
    """Every product of *family* that has tracks, as a :class:`Recording`.

    A family with no products is skipped: the catalogue keeps entries whose
    editions have all been withdrawn, and they carry a headline and a release
    date but nothing to record.
    """
    family_id = family.get("idRaw")
    if not isinstance(family_id, int):
        return
    family_title = _text(family.get("headline"))
    for edge in (family.get("products") or {}).get("edges") or []:
        node = edge.get("node") if isinstance(edge, dict) else None
        if not isinstance(node, dict):
            continue
        recording = _recording(node, family, family_id, slug, family_title)
        if recording is not None:
            yield recording


def _track(
    raw_track: dict[str, Any],
    carrier_name: str | None,
    work_title: str,
    composers: list[Credit],
    from_heading: bool,
) -> Track:
    """One track, with the composers already resolved for it."""
    number = raw_track.get("titleNumber")
    duration = raw_track.get("duration")
    return Track(
        number=number if isinstance(number, int) else None,
        title=_text(raw_track.get("title")) or work_title,
        carrier=carrier_name,
        isrc=_text(raw_track.get("isrc")),
        duration=duration if isinstance(duration, int) else None,
        location=_text(raw_track.get("recordingLocation")),
        recorded_on=_recorded_on(raw_track),
        composers=tuple(credit.name for credit in composers),
        from_heading=from_heading,
    )


def _sort_credits(credits: list[Credit]) -> tuple[list[Credit], list[Credit]]:
    """Split a track's credits into performers and production.

    Composers and the people who wrote the text are in neither list: a composer
    is not a performer on their own recording, and a librettist credited as a
    participant would read as one who sang.
    """
    participants: list[Credit] = []
    production: list[Credit] = []
    for credit in credits:
        if _is_authoring(credit.function) or credit.function.casefold() in _NON_PERFORMING:
            continue
        (production if _is_production(credit.function) else participants).append(credit)
    return participants, production


@dataclass
class _Product:
    """What one product's tracks add up to, accumulated as they are read."""

    by_work: dict[int, tuple[str, list[Track]]] = field(default_factory=dict)
    participants: list[Credit] = field(default_factory=list)
    composers: list[Credit] = field(default_factory=list)
    production: list[Credit] = field(default_factory=list)
    life_dates: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)

    def read(self, raw_track: dict[str, Any], carrier_name: str | None) -> None:
        """Fold one track in, or ignore it if it names no work."""
        work = _level_one_work(raw_track)
        if work is None:
            return
        work_id, work_title = work
        composers, from_heading = _track_composers(raw_track)
        self.composers.extend(composers)
        self.life_dates.update(heading_life_dates(raw_track))
        participants, production = _sort_credits(_credits(raw_track))
        self.participants.extend(participants)
        self.production.extend(production)
        _title, tracks = self.by_work.setdefault(work_id, (work_title, []))
        tracks.append(_track(raw_track, carrier_name, work_title, composers, from_heading))


def _recording(
    product: dict[str, Any],
    family: dict[str, Any],
    family_id: int,
    slug: str,
    family_title: str | None,
) -> Recording | None:
    product_id = product.get("idRaw")
    if not isinstance(product_id, int):
        return None
    read = _Product()
    for carrier in (product.get("playlist") or {}).get("carriers") or []:
        if not isinstance(carrier, dict):
            continue
        carrier_name = _text(carrier.get("name"))
        for raw_track in carrier.get("tracks") or []:
            if isinstance(raw_track, dict):
                read.read(raw_track, carrier_name)
    if not read.by_work:
        return None
    return Recording(
        product_id=product_id,
        family_id=family_id,
        slug=slug,
        title=_text(product.get("headline")) or family_title or slug,
        release_date=_text(product.get("releaseDate")) or _text(family.get("releaseDate")),
        label=_text(product.get("label")),
        catalogue_number=_text(product.get("sku")),
        format=_text(product.get("configuration")),
        category=_text((product.get("productCategory") or {}).get("name")),
        mentions=tuple(_mention(work_id, title, tracks) for work_id, (title, tracks) in read.by_work.items()),
        participants=tuple(_unique(read.participants)),
        composers=tuple(_unique(read.composers)),
        production=tuple(_unique(read.production)),
        life_dates=read.life_dates,
    )


def _mention(work_id: int, title: str, tracks: list[Track]) -> WorkMention:
    """One work's mention, with the composer its tracks agree on.

    A work spanning many tracks can disagree — a heading fallback on one track,
    a proper credit on the next — so the most-credited name wins and the rest
    stay listed. Ties break on first appearance, which is the disc order.
    """
    counts: Counter[str] = Counter()
    for track in tracks:
        counts.update(track.composers)
    ordered = [name for name, _count in counts.most_common()]
    return WorkMention(
        work_id=work_id,
        title=title,
        composer=ordered[0] if ordered else None,
        composers=tuple(ordered),
        tracks=tuple(tracks),
    )
