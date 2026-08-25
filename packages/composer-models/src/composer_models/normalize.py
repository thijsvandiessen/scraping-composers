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


# A dedup key's wikidata suffix. The base is punctuation-stripped, so the pipe
# can only be the separator this module wrote.
_WIKIDATA_SUFFIX = re.compile(r"\|(Q\d+)$")


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


def wikidata_id(key: str) -> str | None:
    """The QID :func:`dedup_key` folded into ``key``, if it carried one.

    The inverse belongs next to the forward direction: the suffix is how an
    entity records *which* wikidata item it is, and a caller that has to know
    that — the dedupe pass, which treats two QIDs as two people — should not
    have to re-derive the encoding.
    """
    found = _WIKIDATA_SUFFIX.search(key)
    return found.group(1) if found else None
