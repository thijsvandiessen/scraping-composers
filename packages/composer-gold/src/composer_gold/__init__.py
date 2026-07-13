"""Gold layer: promote the silver staging database into a curated copy.

Silver records what sources say plus the matching passes over it. ``promote``
rebuilds the gold database from it, applying the curation rules research
consumers want:

1. drop people with no concerts/recordings/works mentioned,
2. collapse duplicate person entities into their canonical row,
3. prune entities left unreferenced by the above.
"""

from composer_config import settings

from .promote import GoldManifest, PromoteConfig, PromoteStats, promote, read_gold_manifest

DEFAULT_GOLD_DB_PATH = settings.gold_db_path

# Optional sitelink-count threshold for promotion (see ``promote``). Unset leaves
# the extra signal off, so promotion keeps its default performance/work rule.
DEFAULT_MIN_SITELINKS: int | None = settings.gold_min_sitelinks

__all__ = [
    "DEFAULT_GOLD_DB_PATH",
    "DEFAULT_MIN_SITELINKS",
    "GoldManifest",
    "PromoteConfig",
    "PromoteStats",
    "promote",
    "read_gold_manifest",
]
