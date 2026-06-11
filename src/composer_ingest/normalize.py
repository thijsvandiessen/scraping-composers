"""Name normalization for cross-source deduplication.

Sources disagree on formatting: IMSLP uses "Beethoven, Ludwig van", others may
use "Ludwig van Beethoven", with varying accents and punctuation. The dedup
key reduces all of these to the same string: comma-inverted names are flipped
to natural order, diacritics stripped, lowercased, punctuation removed.
"""

from __future__ import annotations

import re
import unicodedata


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
