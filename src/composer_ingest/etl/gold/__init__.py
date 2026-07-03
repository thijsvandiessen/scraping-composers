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

__all__ = ["DEFAULT_GOLD_DB_PATH", "GoldManifest", "PromoteStats", "promote", "read_gold_manifest"]
