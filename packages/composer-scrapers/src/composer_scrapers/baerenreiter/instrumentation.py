"""Reading Bärenreiter's "Instrumentation in detail" list into instruments.

The catalogue states a work's forces as a comma-separated list, one entry per
part, each qualified by trailing parentheticals::

    Piccolo flute (Flute), Flute (2) (Piccolo flute), Horn (4), …, Violin (2), Viola

A parenthetical means one of several things, told apart by its content:

- a count: ``Horn (4)``;
- a choir's voicing: ``Mixed choir (SATB)``;
- instruments the same player also takes up, or that realise the part:
  ``Flute (2) (Piccolo flute)``, ``Basso continuo (Violoncello, Organ)``;
- anything else ("in C", "ripieno") is kept as a qualifier and read no further.

Counts can only be told from qualifiers by position in the list, so the list is
split on commas *outside* parentheses — a continuo's realisation carries commas of
its own.

Each entry's instrument name is resolved through the shared
:mod:`composer_schema.instrumentation` table, the same one the LLM claims path
uses, so "Violoncello" here and "Cello" there are one category. A name the table
does not know leaves the entry's ``instrument`` unset rather than guessed; the
verbatim text is always kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from composer_schema.instrumentation import category_for, members_of, parse_instrumentation

#: A choir's voice parts, as the catalogue abbreviates them: "SATB", "TTBB",
#: "SMezATBarB". Case-sensitive, and at least two parts, so that neither an
#: English word nor a single capital is read as one.
_VOICING = re.compile(r"(?:S|Mez|M|A|T|Bar|B){2,}")

#: The catalogue's own choir codes, "GemCh-SATB" and "FCh-SMezA": gemischter
#: (mixed), Frauen- (female) and Männer- (male) Chor, then the voicing.
_CHOIR_CODE = re.compile(r"(Gem|F|M)?C[hH]-(\w+)")
_CHOIR_KINDS = {"Gem": "mixed choir", "F": "female choir", "M": "male choir", None: "choir"}

_COUNT = re.compile(r"\d+")
#: "Piano (4 hands)": part of the instrument's name, not a qualifier of it.
_HANDS = re.compile(r"\d+\s*hands|\d+-händig", re.IGNORECASE)
_ORDINAL = re.compile(r"^\d+\.\s*")
_AD_LIBITUM = re.compile(r"\s+ad libitum$", re.IGNORECASE)
_SOLO = re.compile(r"[\s-]+solo$", re.IGNORECASE)
#: "Horn in F", "Clarinet in B flat": the transposition, which is not a
#: different instrument for the purposes of what a work is scored for.
_KEY = re.compile(r"\s+in\s+[A-G](?:[- ]?(?:flat|sharp)|b|is|es)?$", re.IGNORECASE)
_ALTERNATIVES = re.compile(r"\s+(?:or|oder)\s+")
_ALSO = re.compile(r"^(?:also|or|auch)\s+", re.IGNORECASE)
#: A solo voice written as its voicing letter, "S-solo" or "Mez-solo", which the
#: work's "Scoring" field uses. Only read with "solo" attached: a bare "A" or "B"
#: is too short to trust.
_VOICE_LETTERS = {
    "S": "soprano",
    "Mez": "mezzo-soprano",
    "A": "alto",
    "T": "tenor",
    "Bar": "baritone",
    "B": "bass",
}


@dataclass(frozen=True)
class Part:
    """One entry of the list: what the page said, and what could be read from it."""

    stated: str
    #: Canonical category (see :data:`composer_schema.instrumentation.CATEGORIES`),
    #: or ``None`` when the table does not know the name.
    instrument: str | None = None
    count: int | None = None
    #: Categories the same player(s) also play, or that realise the part.
    also: tuple[str, ...] = ()
    voicing: str | None = None
    solo: bool = False
    ad_libitum: bool = False
    #: Parentheticals that are none of the above, verbatim.
    qualifiers: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        """The part for a document's ``raw`` payload; empty fields left out."""
        out: dict[str, object] = {"stated": self.stated, "instrument": self.instrument}
        if self.count is not None:
            out["count"] = self.count
        if self.also:
            out["also"] = list(self.also)
        if self.voicing:
            out["voicing"] = self.voicing
        if self.solo:
            out["solo"] = True
        if self.ad_libitum:
            out["ad_libitum"] = True
        if self.qualifiers:
            out["qualifiers"] = list(self.qualifiers)
        return out


@dataclass(frozen=True)
class Detail:
    """A whole instrumentation list, part by part."""

    parts: tuple[Part, ...] = field(default_factory=tuple)

    @property
    def instruments(self) -> tuple[str, ...]:
        """Every category the list names, including doublings, in list order."""
        found = [name for part in self.parts for name in (part.instrument, *part.also) if name]
        return tuple(dict.fromkeys(found))

    @property
    def unmatched(self) -> tuple[str, ...]:
        """The entries whose instrument could not be resolved, for growing the table."""
        return tuple(part.stated for part in self.parts if part.instrument is None)


def split_parts(text: str) -> list[str]:
    """*text* split on the commas that are not inside parentheses."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [stripped for part in parts if (stripped := part.strip())]


def _trailing_paren(text: str) -> tuple[str, str] | None:
    """``(rest, inner)`` when *text* ends in a balanced parenthetical, else ``None``."""
    if not text.endswith(")"):
        return None
    depth = 0
    for index in range(len(text) - 1, -1, -1):
        if text[index] == ")":
            depth += 1
        elif text[index] == "(":
            depth -= 1
            if depth == 0:
                return text[:index].rstrip(), text[index + 1 : -1].strip()
    return None


def _instrument_list(text: str) -> tuple[str, ...] | None:
    """The categories of a parenthetical listing instruments, or ``None`` unless
    every entry in it resolves — half a list read is a list misread."""
    entries = [_ALSO.sub("", entry) for piece in split_parts(text) for entry in _ALTERNATIVES.split(piece)]
    found = [category_for(entry) for entry in entries if entry]
    if not found or not all(found):
        return None
    return tuple(name for name in found if name is not None)


def _resolve(name: str) -> str | None:
    """The category of a bare instrument name, alternatives included.

    "Organ or Piano" names no one instrument, so it resolves only when every
    alternative is the same category ("Gemischter Chor (SATB) oder Gemischter Chor").
    """
    alternatives = _ALTERNATIVES.split(name)
    if len(alternatives) == 1:
        return category_for(name)
    found = {parse_part(alternative).instrument for alternative in alternatives}
    return found.pop() if len(found) == 1 else None


@dataclass
class _Reading:
    """What has been read off one entry so far; ``name`` shrinks as it is read."""

    name: str
    count: int | None = None
    voicing: str | None = None
    also: list[str] = field(default_factory=list[str])
    qualifiers: list[str] = field(default_factory=list[str])
    ad_libitum: bool = False
    solo: bool = False


def _read_suffixes(reading: _Reading) -> None:
    """Qualifiers written *after* the parentheticals, which have to come off
    first or the parentheticals would not be trailing: "Trombone (3) ad libitum",
    "Mixed choir (2): SSAB"."""
    if _AD_LIBITUM.search(reading.name):
        reading.name, reading.ad_libitum = _AD_LIBITUM.sub("", reading.name), True
    head, colon, tail = reading.name.partition(":")
    if colon and _VOICING.fullmatch(tail.strip()):
        reading.name, reading.voicing = head.strip(), tail.strip()


def _read_parentheticals(reading: _Reading) -> None:
    """Peel the trailing parentheticals one at a time, from the last inwards."""
    while (peeled := _trailing_paren(reading.name)) is not None:
        reading.name, inner = peeled
        if _COUNT.fullmatch(inner) and reading.count is None:
            reading.count = int(inner)
        elif _VOICING.fullmatch(inner) and reading.voicing is None:
            reading.voicing = inner
        elif _HANDS.fullmatch(inner):
            reading.name = f"{reading.name} {inner}"
        elif inner.casefold() == "ad libitum":
            reading.ad_libitum = True
        elif (instruments := _instrument_list(inner)) is not None:
            reading.also[:0] = instruments
        else:
            reading.qualifiers.insert(0, inner)


def _read_name(reading: _Reading) -> None:
    """What is left around the instrument's name: "1. Violin", "Violin solo",
    "Horn in F"."""
    reading.name = _ORDINAL.sub("", reading.name)
    _read_suffixes(reading)
    reading.solo = bool(_SOLO.search(reading.name))
    reading.name = _SOLO.sub("", reading.name)
    if key := _KEY.search(reading.name):
        reading.qualifiers.insert(0, key.group(0).strip())
        reading.name = reading.name[: key.start()]
    reading.name = reading.name.strip()


def parse_part(stated: str) -> Part:
    """Read one entry of the list; see the module docstring for the rules."""
    stated = " ".join(stated.split())
    if code := _CHOIR_CODE.fullmatch(stated):
        return Part(stated=stated, instrument=_CHOIR_KINDS[code.group(1)], voicing=code.group(2))
    if _VOICING.fullmatch(stated):
        return Part(stated=stated, instrument="choir", voicing=stated)

    reading = _Reading(name=stated)
    _read_suffixes(reading)
    _read_parentheticals(reading)
    _read_name(reading)
    instrument = (_VOICE_LETTERS.get(reading.name) if reading.solo else None) or _resolve(reading.name)
    return Part(
        stated=stated,
        instrument=instrument,
        count=reading.count,
        also=tuple(name for name in dict.fromkeys(reading.also) if name != instrument),
        voicing=reading.voicing,
        solo=reading.solo,
        ad_libitum=reading.ad_libitum,
        qualifiers=tuple(reading.qualifiers),
    )


def parse_detail(text: str | None) -> Detail:
    """Every entry of an instrumentation list, in order."""
    if not text:
        return Detail()
    return Detail(parts=tuple(parse_part(part) for part in split_parts(text)))


def scoring_categories(text: str | None) -> tuple[str, ...]:
    """What a work is *for*, read from the catalogue's "Scoring" field.

    Unlike the detail list, this field names the work's forces as categories —
    "Violin, Orchestra", "Soloists, Mixed choir, Orchestra" — so each comma-separated
    entry is one thing the work is for. Entries that name a combination
    ("Piano (4 hands)") expand to the instruments in it, as
    :func:`~composer_schema.instrumentation.parse_instrumentation` does.
    """
    found: list[str] = []
    for part in parse_detail(text).parts:
        if part.instrument is not None:
            found.extend(parse_instrumentation(part.instrument) or (part.instrument,))
    return tuple(dict.fromkeys(found))


#: A string section with its size: "str (4.4.3.2.1)", "Str (8,0,3,2,1)",
#: "Str. (mindestens 12, 10, 8, 7, 5)" — players (or desks) of first and second
#: violins, violas, cellos and double basses, always in that order.
_STRING_SECTION = re.compile(
    r"\b(?:str|streicher)\.?\s*\((?:mind(?:estens|\.)?\s*)?"
    r"(\d+)\s*[.,]\s*(\d+)\s*[.,]\s*(\d+)\s*[.,]\s*(\d+)\s*[.,]\s*(\d+)\s*\)",
    re.IGNORECASE,
)
_STRING_DESKS = ("violin I", "violin II", "viola", "cello", "double bass")


def string_section(text: str | None) -> dict[str, int] | None:
    """The sizes a shorthand gives the string section, or ``None`` if it gives none.

    Keyed by part ("violin I" … "double bass"); a zero says the part is absent.
    """
    if not text or not (found := _STRING_SECTION.search(text)):
        return None
    return dict(zip(_STRING_DESKS, (int(group) for group in found.groups()), strict=True))


def string_instruments(section: dict[str, int]) -> tuple[str, ...]:
    """The instrument categories a sized string section includes."""
    found = ["violin" if part.startswith("violin") else part for part, size in section.items() if size]
    return tuple(dict.fromkeys(found))


def ensemble_members(categories: tuple[str, ...]) -> tuple[str, ...]:
    """The instruments the named ensembles are made of ("String quartet" -> violin, …)."""
    return members_of(categories)
