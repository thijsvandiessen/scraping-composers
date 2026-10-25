"""Extract structured features from a raw work title.

Classical titles encode a lot of hard identity: catalogue numbers (``BWV 846``,
``K. 331``), opus numbers (``Op. 67``), the musical key (``in C minor`` /
``c-moll``), the work type (symphony, sonata, ...), and a number (``No. 5``).
Pulling these out lets the matcher compare works on stable identifiers rather
than on fuzzy title text alone. ``core_title`` is what remains once the
structured spans are stripped — the residual (a nickname like "Eroica", or just
the type) used for the fuzzy tie-break.

Catalogue numbers are read per composer (see :mod:`composer_schema.catalogues`):
``K. 550`` and ``KV 550`` are one Köchel number for Mozart, and a bare ``D 960``
only counts for Schubert. A range or list (``BWV 1046-1051``) is one number
whose value is the set, never its first piece. ``identifiers`` holds every
``(scheme, value)`` the title and the source's structured fields state -- the
strong keys the matcher tries before any title comparison.

A title that names a part, an arrangement or a version of another work ("Allegro
(from Brandenburg Concerto No. 3, BWV 1048)", "... (arr. for wind quintet)",
"Sinfonie KV 550 (1. Fassung)") shares that work's number without being it. Its
``derivation`` says which, and its catalogue references are the parent's: they
go into ``parent_identifiers`` instead, for the link pass
(:mod:`composer_warehouse.works.links`) to point it at its parent.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from composer_models.normalize import MAX_KEY_CHARS
from composer_schema.catalogues import (
    OPUS,
    catalogue_members,
    find_catalogues,
    is_catalogue_set,
    parse_catalogue,
)

#: What a derived work is of its parent; the ``work_links`` relation is
#: ``f"{derivation}_of"``.
DERIVATIONS = ("arrangement", "part", "version")


@dataclass(frozen=True)
class WorkFeatures:
    normalized_title: str
    core_title: str
    work_type: str | None = None
    opus_number: str | None = None
    catalogue_prefix: str | None = None
    catalogue_number: str | None = None
    musical_key: str | None = None
    number: int | None = None
    # Strong keys: (scheme, value) catalogue numbers and external ids.
    identifiers: frozenset[tuple[str, str]] = frozenset()
    # part | arrangement | version, when the title names one of another work
    derivation: str | None = None
    # The parent work's catalogue numbers, for a derived work.
    parent_identifiers: frozenset[tuple[str, str]] = frozenset()


# A part names its parent in a parenthesis: "(from ...)", "(aus ...)".
_PART = re.compile(r"\((?:from|aus|extraits?\s+de)\s[^)]*\)?", re.I)
_DERIVATION_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "arrangement",
        re.compile(r"\b(?:arr(?:\.|\b)|arranged|bearb\.|bearbeitung|transcr|orchestrated)", re.I),
    ),
    ("part", _PART),
    (
        "version",
        re.compile(
            r"\b(?:\d+\.\s*fassung|(?:erste|zweite|dritte)\s+fassung|urfassung"
            r"|(?:first|original|revised|\d{4})\s+version)\b",
            re.I,
        ),
    ),
)
_OPUS = re.compile(r"\bop(?:us|\.)?\s*(\d+)\s*(?:(?:no|nr|n°|n)\.?\s*(\d+))?", re.I)
_NUMBER = re.compile(r"(?:no|nr|n°)\.?\s*(\d+)", re.I)
_KEY_EN = re.compile(r"\bin\s+([a-g])(?:[-\s]+(sharp|flat))?[-\s]+(major|minor)\b", re.I)
_KEY_DE = re.compile(r"\b([a-h])(?:is|es|s)?-(dur|moll)\b", re.I)

_TYPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("symphony", ("symphony", "symphonie", "sinfonie", "sinfonia")),
    ("concerto", ("concerto", "konzert", "concertos")),
    ("sonata", ("sonata", "sonate", "sonatas")),
    ("quartet", ("quartet", "quartett", "quatuor")),
    ("quintet", ("quintet", "quintett")),
    ("trio", ("trio",)),
    ("mass", ("missa", "messe", " mass")),
    ("requiem", ("requiem",)),
    ("opera", ("opera ", "oper ")),
    ("cantata", ("cantata", "kantate")),
    ("oratorio", ("oratorio", "oratorium")),
    ("overture", ("overture", "ouverture", "ouvertuere")),
    ("prelude", ("prelude", "praludium", "preludio")),
    ("fugue", ("fugue", "fuga", "fuge")),
    ("suite", ("suite",)),
    ("ballet", ("ballet", "ballett")),
    ("tone poem", ("tone poem", "symphonic poem", "sinfonische dichtung")),
    ("lied", ("lied",)),
)


def _strip_diacritics(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def normalize_title(raw: str) -> str:
    """Lowercase, strip diacritics and punctuation, collapse whitespace.

    Truncated to ``MAX_KEY_CHARS``: the result is stored in ``title_key``,
    which sits in the ``uq_work_title`` btree index that Postgres caps.
    """
    text = _strip_diacritics(raw).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:MAX_KEY_CHARS]


def _normalize_key(letter: str, accidental: str | None, mode: str) -> str:
    parts = [letter.lower()]
    if accidental and accidental.lower() in {"sharp", "flat"}:
        parts.append(accidental.lower())
    parts.append("major" if mode.lower() in {"major", "dur"} else "minor")
    return " ".join(parts)


def _find_catalogue(text: str, composer: str | None, spans: list[tuple[int, int]]) -> list[tuple[str, str]]:
    """Every catalogue reference in the title, normalised, first first."""
    found: list[tuple[str, str]] = []
    for ref in find_catalogues(text, composer):
        spans.append(ref.span)
        if (ref.scheme, ref.value) not in found:
            found.append((ref.scheme, ref.value))
    return found


def _without_enclosing_sets(catalogues: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop a range or list that holds one of the title's own numbers: "Suite 6,
    BWV 817 (Französische Suiten BWV 812-817)" is BWV 817, and sharing the set's
    value would match it to the set."""
    singles = {pair for pair in catalogues if not is_catalogue_set(pair[1])}
    return [
        (scheme, value)
        for scheme, value in catalogues
        if not is_catalogue_set(value) or not any((scheme, m) in singles for m in catalogue_members(value))
    ]


def derivation_of(raw_title: str) -> str | None:
    """part | arrangement | version when the title says it is one, else None."""
    return next((name for name, cue in _DERIVATION_CUES if cue.search(raw_title)), None)


def _find_opus_and_number(text: str, spans: list[tuple[int, int]]) -> tuple[str | None, int | None]:
    opus_number = None
    number = None
    opus_m = _OPUS.search(text)
    if opus_m:
        opus_number = opus_m.group(1)
        if opus_m.group(2):
            number = int(opus_m.group(2))
        spans.append(opus_m.span())

    if number is None:
        num_m = _NUMBER.search(text)
        if num_m:
            number = int(num_m.group(1))
            spans.append(num_m.span())
    return opus_number, number


def _find_key(text: str, spans: list[tuple[int, int]]) -> str | None:
    key_m = _KEY_EN.search(text)
    if key_m:
        spans.append(key_m.span())
        return _normalize_key(key_m.group(1), key_m.group(2), key_m.group(3))
    key_m = _KEY_DE.search(text)
    if key_m:
        spans.append(key_m.span())
        return _normalize_key(key_m.group(1), None, key_m.group(2))
    return None


def extract_features(
    raw_title: str,
    *,
    composer: str | None = None,
    structured: Iterable[str] = (),
    stated: Iterable[tuple[str, str]] = (),
) -> WorkFeatures:
    """The features of a title by ``composer`` (whose catalogues decide what a
    bare ``K``/``D``/``S`` means), plus the catalogue numbers a source states
    in ``structured`` fields of its own (IMSLP's "Opus/Catalogue Number") or
    has already normalised into ``stated`` pairs (Wikidata's codes, read by
    their catalogue item)."""
    text = _strip_diacritics(raw_title).lower()
    spans: list[tuple[int, int]] = []

    catalogues = _find_catalogue(text, composer, spans)
    opus_number, number = _find_opus_and_number(text, spans)
    pairs = [pair for field in structured for pair in sorted(parse_catalogue(field, composer))]
    for scheme, value in [*pairs, *stated]:
        if scheme == OPUS:
            # an opus stays on the opus rule (see match._identifier_score)
            if opus_number is None:
                opus_number, _, within = value.partition("/")
                if within and number is None:
                    number = int(within)
        elif (scheme, value) not in catalogues:
            catalogues.append((scheme, value))
    catalogues = _without_enclosing_sets(catalogues)
    musical_key = _find_key(text, spans)
    derivation = derivation_of(raw_title)
    if derivation == "part" and (parent := _PART.search(text)) is not None:
        # "Allegro (from Brandenburg Concerto No. 3, BWV 1048)": the clause
        # names the parent, so two movements of it would look alike by title.
        spans.append(parent.span())

    normalized = normalize_title(raw_title)
    work_type = next((canon for canon, kws in _TYPES if any(kw in normalized for kw in kws)), None)

    # core_title: the normalized title minus the structured spans (but keeping
    # the type word and any nickname) — the residual for fuzzy comparison.
    chars = list(text)
    for start, end in spans:
        for i in range(start, end):
            chars[i] = " "
    core_title = normalize_title("".join(chars))

    refs = frozenset(catalogues)
    catalogue_prefix, catalogue_number = catalogues[0] if catalogues else (None, None)
    return WorkFeatures(
        normalized_title=normalized,
        core_title=core_title,
        work_type=work_type,
        opus_number=opus_number,
        catalogue_prefix=catalogue_prefix,
        catalogue_number=catalogue_number,
        musical_key=musical_key,
        number=number,
        identifiers=frozenset() if derivation else refs,
        derivation=derivation,
        parent_identifiers=refs if derivation else frozenset(),
    )
