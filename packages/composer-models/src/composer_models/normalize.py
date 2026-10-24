"""Name normalization for cross-source deduplication.

Sources disagree on formatting: IMSLP uses "Beethoven, Ludwig van", others may
use "Ludwig van Beethoven", with varying accents and punctuation. The dedup
key reduces all of these to the same string: comma-inverted names are flipped
to natural order, diacritics stripped, lowercased, punctuation removed.
"""

from __future__ import annotations

import re
import unicodedata
import uuid

# Fixed project namespace — never change this; it seeds all entity UUIDs.
_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "scraping-composers")

# Dedup and title keys sit in unique btree indexes, and Postgres caps an index
# tuple at ~2704 bytes — a limit SQLite doesn't have. Scrapers occasionally
# emit a whole index page as one name (the longest key seen in production is
# ~1900 chars), so bound the keys well below that ceiling rather than letting a
# malformed record fail an insert. Two names identical for 512 normalized
# characters are the same entity by any reasonable reading.
MAX_KEY_CHARS = 512


def entity_uuid(kind: str, key: str) -> uuid.UUID:
    """Deterministic UUID for an entity derived from its kind and dedup key.

    Stable across database recreations: the same (kind, key) always produces
    the same UUID, making entity IDs safe to use in external systems.
    """
    return uuid.uuid5(_NAMESPACE, f"{kind}:{key}")


def event_uuid(kind: str, source_name: str, external_key: str) -> uuid.UUID:
    """Deterministic UUID for a derived concert or recording.

    Keyed on the source's *name* and the source's own event key, not on
    ``sources.id`` or derive order, so the id survives re-derives, silver
    rebuilds and promotes — the public API serves it in ``/concerts/{id}``.
    The NUL separator keeps these out of ``entity_uuid``'s space.
    """
    return uuid.uuid5(_NAMESPACE, f"{kind}\x00{source_name}\x00{external_key}")


# A dedup key's wikidata suffix. The base is punctuation-stripped, so the pipe
# can only be the separator this module wrote.
_WIKIDATA_SUFFIX = re.compile(r"\|(Q\d+)$")

# A key that is nothing but the QID (see :func:`qid_key`). The colon cannot
# survive :func:`dedup_key`'s punctuation strip, so no label key looks like one.
_QID_KEY = re.compile(r"^wikidata:(Q\d+)$")

#: Kinds whose entity is the Wikidata item itself, label or no label. A genre
#: or movement is a vocabulary term: its QID names it, and its label is display
#: text Wikidata may rename. ``label|QID`` (what :func:`dedup_key` writes for a
#: person) would split one genre into two entities on a rename — and a silver
#: rebuild replays every bronze run, old labels included (#349). An ensemble
#: with a QID is that orchestra under whatever name it is printed (#350).
QID_KEYED_KINDS = frozenset({"genre", "movement", "ensemble"})


def dedup_key(name: str, wikidata_id: str | None = None) -> str:
    name = name.strip()
    if "," in name:
        last, _, rest = name.partition(",")
        name = f"{rest.strip()} {last.strip()}"
    # strip diacritics: é -> e
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower()
    name = re.sub(r"[^\w\s]", "", name)
    base = re.sub(r"\s+", " ", name).strip()[:MAX_KEY_CHARS]
    if wikidata_id:
        return f"{base}|{wikidata_id}"
    return base


def qid_key(qid: str) -> str:
    """The dedup key of a :data:`QID_KEYED_KINDS` entity pinned to ``qid``."""
    return f"wikidata:{qid}"


def entity_key(kind: str, label: str, wikidata_id: str | None = None) -> str:
    """The dedup key an entity of ``kind`` gets.

    A :data:`QID_KEYED_KINDS` entity with a QID is keyed on the QID alone; every
    other entity — and one of those kinds that arrives without a QID, like a
    genre a non-Wikidata source names — keeps :func:`dedup_key`'s label key.
    :func:`dedup_key` itself stays as it is: it seeds the person ids.
    """
    if wikidata_id and kind in QID_KEYED_KINDS:
        return qid_key(wikidata_id)
    return dedup_key(label, wikidata_id)


def wikidata_id(key: str) -> str | None:
    """The QID ``key`` carries, whichever form wrote it: :func:`dedup_key`'s
    ``label|QID`` suffix or :func:`qid_key`'s ``wikidata:QID``.

    The inverse belongs next to the forward direction: the key is how an
    entity records *which* wikidata item it is, and a caller that has to know
    that — the dedupe pass, which treats two QIDs as two people — should not
    have to re-derive the encoding.
    """
    found = _WIKIDATA_SUFFIX.search(key) or _QID_KEY.match(key)
    return found.group(1) if found else None
