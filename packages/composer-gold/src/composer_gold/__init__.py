"""Gold layer: promote the raw (bronze) staging database into a curated copy.

Bronze records what sources say, verbatim. ``promote`` rebuilds the gold
database from it, applying the curation rules research consumers want:

1. drop people with no concerts/recordings/works mentioned,
2. collapse duplicate person entities into their canonical row,
3. prune entities left unreferenced by the above.
"""

import os

from .promote import GoldManifest, PromoteStats, promote, read_gold_manifest

DEFAULT_GOLD_DB_PATH = os.environ.get("GOLD_DB_PATH", "./gold.db")

# Optional sitelink-count threshold for promotion (see ``promote``). Unset leaves
# the extra signal off, so promotion keeps its default performance/work rule.
_min_sitelinks_env = os.environ.get("GOLD_MIN_SITELINKS")
DEFAULT_MIN_SITELINKS: int | None = int(_min_sitelinks_env) if _min_sitelinks_env else None

__all__ = [
    "DEFAULT_GOLD_DB_PATH",
    "DEFAULT_MIN_SITELINKS",
    "GoldManifest",
    "PromoteStats",
    "promote",
    "read_gold_manifest",
]
