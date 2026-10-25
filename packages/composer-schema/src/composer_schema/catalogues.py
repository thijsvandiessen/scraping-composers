"""Catalogue numbers, normalised per composer.

A catalogue number is the strongest hint a title carries about which work it
names, but only once the spellings agree: Köchel appears as ``K. 550``,
``KV 550`` and ``K.550``, Hoboken as ``Hob. XVI:52`` and ``Hob.XVI/52``. And
the one-letter prefixes mean nothing without the composer: ``D`` is Deutsch
for Schubert, ``S`` is Searle for Liszt, ``B`` is Burghauser for Dvořák, ``K``
is Köchel for Mozart but Kirkpatrick for Domenico Scarlatti, and ``L`` is
Lesure for Debussy but Longo for Scarlatti.

:func:`find_catalogues` reads every reference in a text and
:func:`normalize_catalogue` folds one prefix and number into a
``(scheme, value)`` pair, where the scheme names the catalogue (``KV``,
``Kk``, ``Hob``, ``op``...) rather than the spelling a source used. Two works
of one composer with the same pair are the same work; the same scheme with a
different value is a different one.

Some catalogues keep two numberings, and those stay apart: the original
Köchel numbers (``KV``) and the sixth edition's lettered ones (``KV6``, such
as ``173dB``), so ``K. 183/173dB`` yields both. An appendix (``Anh.``) is a
scheme of its own, as is a catalogue's appendix (``BWV Anh.``, ``KV Anh.``).

A **range** or a **list** of numbers names a set of works, not its first
piece: ``BWV 1046-1051`` (also with an en or em dash, "to" or "bis"), ``TWV
40:2-13`` and ``BWV 1046, 1047`` are one reference each, with a value that
lists the set (``1046-1051``, ``40:2-13``, ``1046-1047``) rather than
``1046``. The value is canonical -- members sorted, consecutive ones joined
into a run -- so ``HWV 259, 258, 260 und 261`` and ``HWV 258-261`` agree, and
a value holding ``-`` or ``,`` is always a set (:func:`is_catalogue_set`, with
:func:`catalogue_members` for what it holds). A range that runs backwards
(``BWV 825-4``, a movement number) or improbably far (``D 960 - 1828``, a
year) is not one, and only its first number is read; a list stops at a number
too small to be a sibling (``BWV 57, 8`` is a cantata and its movement).

Opus numbers are read as well (scheme ``op``, value ``27/2`` for "Op. 27
No. 2"), but they are less trustworthy than a catalogue -- publishers
reassigned them -- so the work matcher keeps them on their own rule.

Dependency-free so the scrapers and the warehouse can both use it.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

#: Scheme of an opus number.
OPUS = "op"


@dataclass(frozen=True)
class CatalogueScheme:
    """One catalogue: the scheme ``code`` stored and the ``prefixes``
    (lowercase, without the trailing dot) a title spells it with."""

    code: str
    prefixes: tuple[str, ...]


@dataclass(frozen=True)
class ComposerCatalogues:
    """The catalogues only one composer's prefixes mean.

    A composer label matches when one of its words is ``surname`` (or one of
    ``names``, other spellings of it) and none is in ``exclude_given`` -- the
    given names of a relative with a catalogue of their own (Leopold and
    Franz Xaver Mozart, Michael Haydn).
    """

    surname: str
    schemes: tuple[CatalogueScheme, ...]
    names: tuple[str, ...] = ()
    exclude_given: frozenset[str] = frozenset()


#: Catalogues whose prefix is unambiguous whoever the composer: a source
#: rarely writes "BWV" for anything but Bach.
UNIVERSAL: tuple[CatalogueScheme, ...] = (
    CatalogueScheme("BWV Anh", ("bwv anh",)),
    CatalogueScheme("KV Anh", ("kv anh", "k anh")),
    CatalogueScheme("BWV", ("bwv",)),
    CatalogueScheme("HWV", ("hwv",)),
    CatalogueScheme("TWV", ("twv",)),
    CatalogueScheme("BuxWV", ("buxwv",)),
    CatalogueScheme("SWV", ("swv",)),
    CatalogueScheme("RV", ("rv",)),
    CatalogueScheme("Hob", ("hob",)),
    CatalogueScheme("KV", ("kv",)),
    # Composer-scoped by construction: works are only ever compared within
    # one composer's catalogue, so Beethoven's WoO 59 and Brahms's WoO 59
    # never meet.
    CatalogueScheme("WoO", ("woo",)),
    CatalogueScheme("Anh", ("anh",)),
)

COMPOSERS: tuple[ComposerCatalogues, ...] = (
    ComposerCatalogues(
        surname="mozart",
        # K and KV are both Köchel; a lettered number is the sixth edition's.
        schemes=(CatalogueScheme("KV", ("k", "kv")),),
        exclude_given=frozenset({"leopold", "franz", "xaver"}),
    ),
    ComposerCatalogues(
        surname="scarlatti",
        schemes=(CatalogueScheme("Kk", ("k", "kk")), CatalogueScheme("L", ("l",))),
        exclude_given=frozenset({"alessandro", "pietro", "giuseppe"}),
    ),
    ComposerCatalogues(
        surname="schubert",
        schemes=(CatalogueScheme("D", ("d",)),),
        exclude_given=frozenset({"anton", "ferdinand"}),
    ),
    ComposerCatalogues(
        surname="liszt",
        schemes=(CatalogueScheme("S", ("s",)), CatalogueScheme("LW", ("lw",))),
    ),
    ComposerCatalogues(surname="dvorak", schemes=(CatalogueScheme("B", ("b",)),)),
    ComposerCatalogues(surname="debussy", schemes=(CatalogueScheme("L", ("l",)),)),
    ComposerCatalogues(
        surname="holst",
        schemes=(CatalogueScheme("H", ("h",)),),
        exclude_given=frozenset({"imogen"}),
    ),
    ComposerCatalogues(surname="honegger", schemes=(CatalogueScheme("H", ("h",)),)),
    ComposerCatalogues(surname="martinu", schemes=(CatalogueScheme("H", ("h",)),)),
)

_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}


def _ascii_words(text: str) -> list[str]:
    folded = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return re.findall(r"[a-z]+", folded.lower())


def composer_catalogues(composer_label: str | None) -> ComposerCatalogues | None:
    """The composer-specific catalogues for a composer label, if any."""
    if not composer_label:
        return None
    words = set(_ascii_words(composer_label))
    for entry in COMPOSERS:
        if (entry.surname in words or words & set(entry.names)) and not words & entry.exclude_given:
            return entry
    return None


def _scheme_for(prefix: str, composer: ComposerCatalogues | None) -> str | None:
    key = re.sub(r"[\s.]+", " ", prefix.lower()).strip()
    if composer is not None:
        for scheme in composer.schemes:
            if key in scheme.prefixes:
                return scheme.code
    return next((scheme.code for scheme in UNIVERSAL if key in scheme.prefixes), None)


def _roman_to_int(numeral: str) -> int | None:
    total = 0
    values = [_ROMAN.get(c) for c in numeral.lower()]
    if None in values or not values:
        return None
    ints = [v for v in values if v is not None]
    for i, value in enumerate(ints):
        total += -value if i + 1 < len(ints) and value < ints[i + 1] else value
    return total


def _int_to_roman(number: int) -> str:
    out = ""
    for value, numeral in ((100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX")):
        while number >= value:
            out += numeral
            number -= value
    for value, numeral in ((5, "V"), (4, "IV"), (1, "I")):
        while number >= value:
            out += numeral
            number -= value
    return out


_HOB = re.compile(r"^(?P<group>[ivxlc]+|\d+)(?P<suffix>[a-z]{0,2})\s*[:./\s]\s*(?P<number>\d+[a-z]?)$", re.I)
_PLAIN = re.compile(r"^\d+[a-z]{0,3}$", re.I)
_KV6 = re.compile(r"^\d+[a-z]{1,3}$", re.I)


def _hoboken(number: str) -> str | None:
    """``XVI:52`` from ``XVI/52``, ``xvi.52`` or ``16:52``."""
    m = _HOB.match(number.strip())
    if m is None:
        return None
    group = m.group("group")
    if group.isdigit():
        group = _int_to_roman(int(group))
    elif _roman_to_int(group) is None:
        return None
    return f"{group.upper()}{m.group('suffix').lower()}:{m.group('number').lower()}"


def _single_values(scheme: str, number: str) -> tuple[tuple[str, str], ...]:
    if scheme == "Hob":
        value = _hoboken(number)
        return ((scheme, value),) if value else ()
    number = re.sub(r"\s+", "", number)
    if scheme == "KV":
        pairs = [("KV6" if _KV6.match(part) else "KV", part.lower()) for part in number.split("/")]
        return tuple(dict.fromkeys(pair for pair in pairs if _PLAIN.match(pair[1])))
    if scheme == "TWV" and re.fullmatch(r"\d+:(?:[a-z]+\d*|\d+[a-z]?)", number, re.I):
        # Telemann: genre, then key and number ("55:e1") or a number ("40:2").
        return ((scheme, number.lower()),)
    if scheme == "LW" and re.fullmatch(r"[a-z]\d+[a-z]?", number, re.I):
        # The Liszt-Werkverzeichnis numbers within lettered sections.
        return ((scheme, number.lower()),)
    if re.fullmatch(r"\d+[a-z]{0,3}(?:/\d+[a-z]{0,3})?", number, re.I):
        return ((scheme, number.lower()),)
    return ()


#: How far a range may run before its end reads as something else (a year).
MAX_RANGE = 500
#: The longest set value kept: ``works.catalogue_number`` holds 50 characters.
MAX_SET_CHARS = 50

_RANGE_SEP = r"\s*[-\u2013\u2014]\s*|\s+(?:to|bis)\s+"
_LIST_SEP = r"\s*,\s+|\s*&\s*|\s+(?:and|und|et)\s+"
_ITEM = re.compile(rf"(?P<start>.+?)(?:(?:{_RANGE_SEP})(?P<end>\d+))?$", re.I | re.S)
_LIST_SPLIT = re.compile(rf"(?:{_LIST_SEP})(?=\d)", re.I)
_MEMBER = re.compile(r"^(?P<head>.*?)(?P<n>\d+)(?P<suffix>[a-z]*)$")
_RUN = re.compile(r"^(?P<head>.*?)(?P<start>\d+)-(?P<end>\d+)$")
_YEAR = re.compile(r"^(?:1[5-9]|20)\d\d$")


def _range_members(value: str, end: str) -> list[str] | None:
    """``40:2`` .. ``40:13`` for ``40:2`` to ``13``; None unless it runs forward,
    and not too far, from a value ending in a plain number."""
    m = re.fullmatch(r"(?P<head>.*?)(?P<n>\d+)", value)
    if m is None:
        return None
    start, stop = int(m.group("n")), int(end)
    if not start < stop <= start + MAX_RANGE:
        return None
    return [f"{m.group('head')}{n}" for n in range(start, stop + 1)]


def _canonical(members: list[str]) -> str:
    """Members sorted, with each run of consecutive plain numbers joined."""

    def key(member: str) -> tuple[str, int, str]:
        m = _MEMBER.match(member)
        return (m.group("head"), int(m.group("n")), m.group("suffix")) if m else (member, -1, "")

    parts: list[str] = []
    run: list[tuple[str, int]] = []

    def flush() -> None:
        if run:
            head, first = run[0]
            parts.append(f"{head}{first}" if len(run) == 1 else f"{head}{first}-{run[-1][1]}")
            run.clear()

    for member in sorted(set(members), key=key):
        head, n, suffix = key(member)
        if suffix or n < 0:
            flush()
            parts.append(member)
        elif run and run[-1] == (head, n - 1):
            run.append((head, n))
        else:
            flush()
            run.append((head, n))
    flush()
    return ",".join(parts)


def _sibling(item: str, first: str) -> bool:
    """Whether a listed number can be another work beside ``first``: not a
    movement within it (``BWV 57, 8``) nor a year (``BWV 140, 1731``)."""
    n, base = int(re.sub(r"\D+$", "", item)), int(re.sub(r"\D+$", "", first))
    return 2 * n > base and not (_YEAR.match(item) and base < 1000)


def _values(scheme: str, number: str) -> tuple[tuple[str, str], ...]:
    items = [_ITEM.match(item.strip()) for item in _LIST_SPLIT.split(number)]
    first = items[0]
    if first is None:
        return ()
    head = _single_values(scheme, first.group("start"))
    if len(head) != 1 or (first.group("end") is None and len(items) == 1):
        return head
    scheme, value = head[0]
    members = [value] if first.group("end") is None else _range_members(value, first.group("end"))
    if members is None:
        return head  # not a range ("BWV 825-4"): the first number stands
    if _PLAIN.match(value):
        for item in items[1:]:
            if item is None or not _PLAIN.match(start := item.group("start").lower()):
                break
            if not _sibling(start, value):
                break
            more = [start] if item.group("end") is None else _range_members(start, item.group("end"))
            if more is None:
                break
            members += more
    canonical = _canonical(members)
    return ((scheme, canonical),) if len(canonical) <= MAX_SET_CHARS else ()


def is_catalogue_set(value: str) -> bool:
    """Whether a catalogue value names a set of works (a range or a list)."""
    return "-" in value or "," in value


def catalogue_members(value: str) -> frozenset[str]:
    """The single values a catalogue value holds: ``{"40:2", ..., "40:13"}``
    for ``40:2-13``, ``{value}`` for a single number."""
    members: set[str] = set()
    for item in value.split(","):
        m = _RUN.match(item)
        if m is None:
            members.add(item)
        else:
            head = m.group("head")
            members.update(f"{head}{n}" for n in range(int(m.group("start")), int(m.group("end")) + 1))
    return frozenset(members)


def normalize_catalogue(
    prefix: str, number: str, composer_label: str | None = None
) -> tuple[tuple[str, str], ...]:
    """The ``(scheme, value)`` pairs one catalogue reference stands for.

    Usually one pair, none when the prefix is not a catalogue (or is a bare
    letter the composer does not use), and two for a Köchel reference that
    gives both numberings (``K. 183/173dB``). Values are lowercased except a
    Hoboken group's roman numeral. ``number`` may be a range or a list
    (``1046-1051``, ``1046, 1047``), which gives one pair whose value is the
    set.
    """
    scheme = _scheme_for(prefix, composer_catalogues(composer_label))
    return _values(scheme, number) if scheme is not None else ()


# Longest prefixes first so "bwv anh" is not read as "bwv", "buxwv" not as
# "b", "kv" not as "k". The lookbehind makes a prefix start a word, so the
# "l" of "kl.t." (Dutch for minor) is never Lesure.
_PREFIXES = r"bwv\.?\s*anh|kv\.?\s*anh|k\.?\s*anh|buxwv|bwv|hwv|twv|swv|rv|hob|woo|anh|kv|kk|lw|k|d|s|b|l|h"
_CATALOGUE = re.compile(
    rf"(?<![\w.])(?P<prefix>{_PREFIXES})\.?\s*"
    r"(?P<number>(?:(?:[ivxlc]+[a-z]?(?:\s*[:./]\s*|\s+)\d+[a-z]?)"  # Hob. XVI:52
    r"|(?:\d+\s*:\s*[a-z]{1,3}\d*)"  # TWV 55:e1
    r"|(?:\d+[a-z]{0,3}(?:\s*[:/]\s*\d+[a-z]{0,3})?)"
    r"|(?:[a-z]\s?\d+[a-z]?))"  # LW A179
    rf"(?:(?:{_RANGE_SEP})\d+)?"  # BWV 1046-1051
    rf"(?:(?:{_LIST_SEP})\d+[a-z]{{0,3}}(?:(?:{_RANGE_SEP})\d+)?)*)"  # BWV 1046, 1047
    r"(?![\w])",
    re.I,
)
_OPUS = re.compile(r"\bop(?:us|\.)?\s*(\d+)[a-z]?\s*,?\s*(?:(?:no|nr|n°|n)\.?\s*(\d+))?", re.I)


@dataclass(frozen=True)
class CatalogueRef:
    """One reference found in a text, with where it sat."""

    scheme: str
    value: str
    span: tuple[int, int]
    prefix: str
    number: str


def find_catalogues(text: str, composer_label: str | None = None) -> list[CatalogueRef]:
    """Every catalogue reference in ``text`` (opus numbers excluded), in order.

    A bare one-letter prefix counts only when the composer uses that
    catalogue, so "Sonata in D, D 3" is a Deutsch number for Schubert and
    nothing for Beethoven.
    """
    refs: list[CatalogueRef] = []
    for m in _CATALOGUE.finditer(text):
        prefix, number = m.group("prefix"), m.group("number")
        if not any(c.isdigit() for c in number):
            continue
        for scheme, value in normalize_catalogue(prefix, number, composer_label):
            span, written = m.span(), number
            if not is_catalogue_set(value) and (first := _ITEM.match(_LIST_SPLIT.split(number)[0])):
                # a tail that was no range or list ("BWV 57, 8") stays in the text
                written = first.group("start")
                span = (span[0], m.start("number") + len(written))
            refs.append(CatalogueRef(scheme, value, span, prefix, written))
    return refs


def find_opus(text: str) -> tuple[str, int | None, tuple[int, int]] | None:
    """The first opus number in ``text``: (opus, number within it, span)."""
    m = _OPUS.search(text)
    if m is None:
        return None
    return m.group(1), int(m.group(2)) if m.group(2) else None, m.span()


def parse_catalogue(text: str, composer_label: str | None = None) -> frozenset[tuple[str, str]]:
    """Every ``(scheme, value)`` a structured catalogue field states, opus
    included -- for fields like IMSLP's "Op.27 No.2 ; K.331".
    """
    pairs = {(ref.scheme, ref.value) for ref in find_catalogues(text, composer_label)}
    if (opus := find_opus(text)) is not None:
        number, within, _span = opus
        pairs.add((OPUS, f"{number}/{within}" if within is not None else number))
    return frozenset(pairs)
