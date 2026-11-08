"""Folding SPARQL result rows into one SourceRecord per composer.

Birth/death dates are stored at the precision Wikidata records for them (see
``_format_time``): a year-only fact lands as ``1756``, not the padded
``1756-01-01``, so the stored string never overstates how precisely the date is
known. Items (or claim objects) the label service cannot name come back as their
bare QID ("Q12345") and are skipped, since a QID is not a name to dedup on.
See LABEL_LANGUAGES in ``query`` for which labels count as a name.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .. import SourceClaim, SourceRecord

BASE_URL = "https://www.wikidata.org"

log = logging.getLogger(__name__)

# Marker kind for Wikidata time literals, which get truncated to their
# recorded precision (see _format_time). Not an entity kind.
_DATE = "date"

# SPARQL result variable -> (claim predicate, object entity kind).
# kind _DATE means the value is a time literal; kind None means the value is a
# plain string literal stored verbatim.
FIELDS: tuple[tuple[str, str, str | None], ...] = (
    ("birth", "born_on", _DATE),
    ("death", "died_on", _DATE),
    ("birthPlaceLabel", "born_in", "place"),
    ("deathPlaceLabel", "died_in", "place"),
    ("countryLabel", "citizen_of", "place"),
    ("alias", "also_known_as", None),
    # P434 MusicBrainz artist id: carried as a claim so a MusicBrainz source
    # can be linked by identifier instead of fuzzy name matching.
    ("musicbrainz", "musicbrainz_id", None),
)

# MULTI_QUERY item variable -> (claim predicate, object entity kind). These
# objects are pinned by QID, read from the variable itself, with the label from
# ``<var>Label``: a genre or movement entity is keyed on its QID (#349), so a
# label Wikidata renames stays the same entity.
ITEM_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("genre", "has_genre", "genre"),
    ("movement", "in_movement", "movement"),
)

# RELATIONS_QUERY property -> claim predicate; the object is always a person,
# pinned by QID. Each is stored as the subject's own item asserts it: B's P802
# naming A is not copied onto A as student_of, since A's record does not say
# it. Father and mother both read as child_of -- which parent is not a
# relationship between the two people.
RELATIONS: tuple[tuple[str, str], ...] = (
    ("P1066", "student_of"),
    ("P802", "teacher_of"),
    ("P737", "influenced_by"),
    ("P22", "child_of"),
    ("P25", "child_of"),
    ("P40", "parent_of"),
    ("P26", "spouse_of"),
    ("P3373", "sibling_of"),
)
_RELATION_PREDICATES: dict[str, str] = dict(RELATIONS)

# Object kinds whose claims are pinned to the Wikidata item they name. The query
# returns the item beside its label (``birthPlace`` beside ``birthPlaceLabel``),
# so "Vienna" the city and a "Vienna" elsewhere stay apart, and the place record
# the ``wikidata_places`` source fetches for that item lands on the same entity.
_PINNED_KINDS = frozenset({"place"})

# METRICS_QUERY result variable -> claim predicate (all literal-valued).
METRICS: tuple[tuple[str, str], ...] = (
    ("sitelinks", "sitelink_count"),
    ("statements", "statement_count"),
    ("identifiers", "identifier_count"),
    ("works", "work_count"),
)

# What the label service echoes for an item it cannot name: the bare QID, or,
# for an "unknown value" statement, the blank node's skolem IRI. Neither is a
# name, and the IRI is not an item either.
_BARE_QID = re.compile(r"^(Q\d+|http://www\.wikidata\.org/\.well-known/genid/\w+)$")

# A Wikidata time literal: optional BCE sign, year (>=1 digit), zero-padded
# month and day. The time component is split off before this matches.
_TIME = re.compile(r"(-?)(\d+)-(\d{2})-(\d{2})")


def _format_time(value: str, precision: str | None) -> str:
    """Truncate a Wikidata time literal to the granularity its ``precision``
    warrants: day (11) keeps YYYY-MM-DD, month (10) keeps YYYY-MM, year (9) or
    coarser keeps just the year. Wikidata pads unknown components to 01, so
    without this a year-only date masquerades as 1 January. Precision below
    year (decade/century/...) is still rendered as the year and remains a
    coarse approximation; modeling those as ranges is left to downstream.

    Non-time values (e.g. an "unknown value" node that slips through) and a
    missing/garbled precision are passed through / treated as full-precision
    so this never raises."""
    date = value.split("T", 1)[0]  # "1756-01-27T00:00:00Z" -> "1756-01-27"
    match = _TIME.fullmatch(date)
    if match is None:
        return date
    sign, year, month, day = match.groups()
    try:
        prec = int(precision) if precision is not None else 11
    except ValueError:
        prec = 11
    if prec >= 11:
        body = f"{year}-{month}-{day}"
    elif prec == 10:
        body = f"{year}-{month}"
    else:
        body = year
    return sign + body


def _literal(row: dict[str, Any], var: str) -> str | None:
    """The row's value for ``var``, or None if absent or not a plain literal
    (Wikidata's "unknown value" comes back as a blank node)."""
    binding = row.get(var)
    if not binding or binding.get("type") not in ("literal", "uri"):
        return None
    value: str = binding["value"]
    return value


def _relation(row: dict[str, Any]) -> tuple[str, str, str] | None:
    """A RELATIONS_QUERY row as (predicate, other QID, other label), or None
    for any other row -- or one whose other end the label service cannot name."""
    relation, other, label = (_literal(row, var) for var in ("relation", "other", "otherLabel"))
    if relation is None or other is None or label is None or _BARE_QID.match(label):
        return None
    predicate = _RELATION_PREDICATES.get(relation.rsplit("/", 1)[-1])
    if predicate is None:
        return None
    return predicate, other.rsplit("/", 1)[-1], label


def _fold_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group result rows by QID, collecting each field's values into a set."""
    items: dict[str, dict[str, Any]] = {}
    for row in rows:
        uri = row["item"]["value"]
        qid = uri.rsplit("/", 1)[-1]
        item = items.setdefault(
            qid,
            {
                "label": _literal(row, "itemLabel") or "",
                "values": {},
                "pins": {},
                "items": {},
                "relations": set(),
            },
        )
        if (relation := _relation(row)) is not None:
            item["relations"].add(relation)
        for var, _, _ in ITEM_FIELDS:
            obj, label = _literal(row, var), _literal(row, f"{var}Label")
            if obj is None or label is None or _BARE_QID.match(label):
                continue  # unbound, or the object has no label
            item["items"].setdefault(var, set()).add((obj.rsplit("/", 1)[-1], label))
        for var, _, kind in FIELDS:
            value = _literal(row, var)
            if value is None:
                continue
            if kind == _DATE:
                value = _format_time(value, _literal(row, f"{var}Precision"))
            elif kind is not None and _BARE_QID.match(value):
                continue  # claim object has no label
            item["values"].setdefault(var, set()).add(value)
            if kind in _PINNED_KINDS and (pin := _literal(row, var.removesuffix("Label"))) is not None:
                item["pins"].setdefault((var, value), set()).add(pin.rsplit("/", 1)[-1])
    return items


def _item_claims(item: dict[str, Any], item_metrics: dict[str, str]) -> tuple[SourceClaim, ...]:
    # every record matched the occupation=composer query
    claims = [SourceClaim("has_profession", "profession", "composer")]
    for var, predicate, kind in FIELDS:
        for value in sorted(item["values"].get(var, ())):
            if kind is None or kind == _DATE:
                claims.append(SourceClaim(predicate, value=value))
            else:
                # one claim per item the label names; unpinned if the row had none
                pins: list[str | None] = sorted(item["pins"].get((var, value), ())) or [None]
                for pin in pins:
                    claims.append(SourceClaim(predicate, kind, value, object_wikidata_id=pin))
    for var, predicate, kind in ITEM_FIELDS:
        for obj, label in sorted(item["items"].get(var, ())):
            claims.append(SourceClaim(predicate, kind, label, object_wikidata_id=obj))
    for predicate, other, label in sorted(item["relations"]):
        claims.append(SourceClaim(predicate, "person", label, object_wikidata_id=other))
    for var, predicate in METRICS:
        if var in item_metrics:
            claims.append(SourceClaim(predicate, value=item_metrics[var]))
    return tuple(claims)


def _records_from_rows(
    rows: list[dict[str, Any]], metrics: dict[str, dict[str, str]] | None = None
) -> list[SourceRecord]:
    """Fold SPARQL result rows (several per composer when properties have
    multiple values) into one SourceRecord per composer, attaching the
    composer's popularity ``metrics`` (from ``_fetch_metrics``) as claims."""
    records = []
    for qid, item in _fold_rows(rows).items():
        name = item["label"]
        if not name or _BARE_QID.match(name):
            log.debug("skipping %s: no label", qid)
            continue
        item_metrics = (metrics or {}).get(qid, {})
        raw: dict[str, Any] = {"item": f"http://www.wikidata.org/entity/{qid}", "label": name}
        raw.update({var: sorted(values) for var, values in item["values"].items()})
        pinned: dict[str, set[str]] = {}
        for (var, _), pins in item["pins"].items():
            pinned.setdefault(var.removesuffix("Label"), set()).update(pins)
        raw.update({var: sorted(pins) for var, pins in pinned.items()})
        for var, pairs in item["items"].items():
            raw[var] = [{"item": obj, "label": label} for obj, label in sorted(pairs)]
        if item["relations"]:
            raw["relations"] = [
                {"predicate": predicate, "item": other, "label": label}
                for predicate, other, label in sorted(item["relations"])
            ]
        raw.update(item_metrics)
        records.append(
            SourceRecord(
                external_id=qid,
                name=name,
                url=f"{BASE_URL}/wiki/{qid}",
                raw=raw,
                claims=_item_claims(item, item_metrics),
            )
        )
    return records
