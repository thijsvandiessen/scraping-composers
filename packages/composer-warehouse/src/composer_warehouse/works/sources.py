"""Per-source readers for the work identity a mention's payload carries.

Titles are the one thing every source gives, but some sources state more: IMSLP
pages have an "Opus/Catalogue Number" field, and a Wikidata work is itself an
item, with its catalogue codes (P528), MusicBrainz work ids (P435) and ISWCs
(P1827) alongside. The readers here pull those out of ``raw`` so the matcher can
use them; a source without a reader contributes its title alone.

Henle's and Bärenreiter's ``catalogue_number`` claims are edition numbers (HN
1234, BA 5678), not work catalogue numbers, so those sources have no reader:
their titles are what carries the work's number.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from composer_schema.catalogues import OPUS, normalize_wikidata_code

from .extract import WorkFeatures, extract_features
from .match import EXTERNAL_SCHEMES

_QID = re.compile(r"(Q\d+)$")
_MBID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_ISWC = re.compile(r"^T(\d{10})$")


def _qid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = _QID.search(value)
    return match.group(1) if match else None


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]  # pyright: ignore[reportUnknownVariableType]
    return []


def normalize_iswc(value: str) -> str | None:
    """``T0345246801`` from ``T-034.524.680-1``; None if it is not an ISWC."""
    compact = re.sub(r"[\s.\-]", "", value).upper()
    return compact if _ISWC.match(compact) else None


def _imslp_catalogue(raw: dict[str, Any]) -> list[str]:
    # The page's own field, not the worklist's ``imslp_catalogue_number``:
    # that one is IMSLP's internal reference ("ILB 193"), not the work's.
    return _strings(raw.get("opus_catalogue_number"))


def _text(entry: dict[str, Any], key: str) -> str | None:
    value = entry.get(key)
    return value if isinstance(value, str) else None


def _wikidata_catalogue(raw: dict[str, Any], composer: str | None) -> list[tuple[str, str]]:
    """Each P528 code, read by its catalogue item (see
    :func:`composer_schema.catalogues.normalize_wikidata_code`): Wikidata
    states Köchel 550 as "K. 550" on some items and as "550" (catalogue:
    Köchel catalogue, short name "KV") on others, and Terry 238/3 as "238/3"
    in a catalogue with no short name at all."""
    pairs: list[tuple[str, str]] = []
    for entry in raw.get("catalogue_codes") or []:
        if not isinstance(entry, dict) or (code := _text(entry, "code")) is None:  # pyright: ignore[reportUnknownArgumentType]
            continue
        pairs += normalize_wikidata_code(
            code,
            _text(entry, "catalogue_item"),  # pyright: ignore[reportUnknownArgumentType]
            _text(entry, "catalogue_short"),  # pyright: ignore[reportUnknownArgumentType]
            composer,
        )
    return pairs


_CATALOGUE_READERS: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "imslp_works": _imslp_catalogue,
}
_STATED_READERS: dict[str, Callable[[dict[str, Any], str | None], list[tuple[str, str]]]] = {
    "wikidata_catalogue": _wikidata_catalogue,
}


def structured_catalogue(source_name: str, raw: dict[str, Any]) -> list[str]:
    """The catalogue/opus fields a source states apart from the title, as
    text to read for the composer."""
    reader = _CATALOGUE_READERS.get(source_name)
    return reader(raw) if reader is not None else []


def stated_catalogue(source_name: str, raw: dict[str, Any], composer: str | None) -> list[tuple[str, str]]:
    """The ``(scheme, value)`` catalogue numbers a source states already
    resolved to their catalogue, as Wikidata does with each code's item."""
    reader = _STATED_READERS.get(source_name)
    return reader(raw, composer) if reader is not None else []


def external_ids(source_name: str, raw: dict[str, Any]) -> frozenset[tuple[str, str]]:
    """The ``(scheme, value)`` external work ids a mention's payload states."""
    ids: set[tuple[str, str]] = set()
    if source_name in ("wikidata_works", "wikidata_catalogue") and (qid := _qid(raw.get("item"))):
        ids.add(("wikidata", qid))
    if source_name == "wikidata_catalogue":
        mbids = (m.lower() for m in _strings(raw.get("musicbrainz_work")))
        ids |= {("musicbrainz", m) for m in mbids if _MBID.match(m)}
        ids |= {("iswc", i) for value in _strings(raw.get("iswc")) if (i := normalize_iswc(value))}
    return frozenset(ids)


def part_of(source_name: str, raw: dict[str, Any]) -> list[str]:
    """The Wikidata items a mention's work is part of (P361)."""
    if source_name != "wikidata_catalogue":
        return []
    return sorted({qid for value in _strings(raw.get("part_of")) if (qid := _qid(value))})


def mention_features(source_name: str, title: str, composer: str | None, raw: dict[str, Any]) -> WorkFeatures:
    """The features the matcher compares for one mention: its title read for
    ``composer``, plus the structured catalogue fields and external ids its
    source states."""
    features = extract_features(
        title,
        composer=composer,
        structured=structured_catalogue(source_name, raw),
        stated=stated_catalogue(source_name, raw, composer),
    )
    external = external_ids(source_name, raw)
    if not external:
        return features
    return replace(features, identifiers=features.identifiers | external)


def has_catalogue(identifiers: frozenset[tuple[str, str]]) -> bool:
    """Whether any identifier is a catalogue number (not an external id or opus)."""
    return any(scheme not in EXTERNAL_SCHEMES and scheme != OPUS for scheme, _ in identifiers)


__all__ = [
    "external_ids",
    "has_catalogue",
    "mention_features",
    "normalize_iswc",
    "part_of",
    "stated_catalogue",
    "structured_catalogue",
]
