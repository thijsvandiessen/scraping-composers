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


def entity_uuid(kind: str, key: str) -> uuid.UUID:
    """Deterministic UUID for an entity derived from its kind and dedup key.

    Stable across database recreations: the same (kind, key) always produces
    the same UUID, making entity IDs safe to use in external systems.
    """
    return uuid.uuid5(_NAMESPACE, f"{kind}:{key}")


def dedup_key(name: str) -> str:
    name = name.strip()
    if "," in name:
        last, _, rest = name.partition(",")
        name = f"{rest.strip()} {last.strip()}"
    # strip diacritics: é -> e
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower()
    name = re.sub(r"[^\w\s]", "", name)
    return re.sub(r"\s+", " ", name).strip()
