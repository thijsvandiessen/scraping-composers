"""Extract structured features from a raw work title.

Classical titles encode a lot of hard identity: catalogue numbers (``BWV 846``,
``K. 331``), opus numbers (``Op. 67``), the musical key (``in C minor`` /
``c-moll``), the work type (symphony, sonata, ...), and a number (``No. 5``).
Pulling these out lets the matcher compare works on stable identifiers rather
than on fuzzy title text alone. ``core_title`` is what remains once the
structured spans are stripped — the residual (a nickname like "Eroica", or just
the type) used for the fuzzy tie-break.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


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


# Catalogue prefixes (lowercased); single letters are kept but the number that
# follows must contain a digit, so "in d minor" never reads as catalogue "d".
_CATALOGUE = re.compile(
    r"\b(bwv|kv|k|hob|rv|woo|anh|d|s|b|h)\.?\s*([ivxlcdm\d]+(?:[:./-][ivxlcdm\d]+)*)",
    re.I,
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
    """Lowercase, strip diacritics and punctuation, collapse whitespace."""
    text = _strip_diacritics(raw).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_key(letter: str, accidental: str | None, mode: str) -> str:
    parts = [letter.lower()]
    if accidental and accidental.lower() in {"sharp", "flat"}:
        parts.append(accidental.lower())
    parts.append("major" if mode.lower() in {"major", "dur"} else "minor")
    return " ".join(parts)


def extract_features(raw_title: str) -> WorkFeatures:  # noqa: C901
    text = _strip_diacritics(raw_title).lower()
    spans: list[tuple[int, int]] = []

    catalogue_prefix = catalogue_number = None
    for cat_m in _CATALOGUE.finditer(text):
        if any(c.isdigit() for c in cat_m.group(2)):
            catalogue_prefix = cat_m.group(1).upper()
            catalogue_number = cat_m.group(2).upper()
            spans.append(cat_m.span())
            break

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

    musical_key = None
    key_m = _KEY_EN.search(text)
    if key_m:
        musical_key = _normalize_key(key_m.group(1), key_m.group(2), key_m.group(3))
        spans.append(key_m.span())
    else:
        key_m = _KEY_DE.search(text)
        if key_m:
            musical_key = _normalize_key(key_m.group(1), None, key_m.group(2))
            spans.append(key_m.span())

    normalized = normalize_title(raw_title)
    work_type = next((canon for canon, kws in _TYPES if any(kw in normalized for kw in kws)), None)

    # core_title: the normalized title minus the structured spans (but keeping
    # the type word and any nickname) — the residual for fuzzy comparison.
    chars = list(text)
    for start, end in spans:
        for i in range(start, end):
            chars[i] = " "
    core_title = normalize_title("".join(chars))

    return WorkFeatures(
        normalized_title=normalized,
        core_title=core_title,
        work_type=work_type,
        opus_number=opus_number,
        catalogue_prefix=catalogue_prefix,
        catalogue_number=catalogue_number,
        musical_key=musical_key,
        number=number,
    )
