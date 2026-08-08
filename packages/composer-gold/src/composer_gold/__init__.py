"""Gold layer: promote the silver staging database into a curated copy.

Silver records what sources say plus the matching passes over it. ``promote``
rebuilds the gold database from it, applying the curation rules research
consumers want:

1. drop people and ensembles credited on fewer than ``min_appearances``
   concerts/recordings, unless they composed a work some source mentioned,
3. prune entities referenced by fewer than ``min_referrers`` distinct kept
   persons (default 1: keep anything referenced at all).

(Rule 2 collapsed duplicate person entities into a canonical row; it and the
person-dedupe pass behind it are gone. The remaining rules keep their numbers,
which the code and the CLI help text refer to throughout.)
"""

from composer_config import settings

from .promote import GoldManifest, PromoteConfig, PromoteStats, promote, read_gold_manifest

DEFAULT_GOLD_DB_PATH = settings.gold_db_path

# Optional sitelink-count threshold for promotion (see ``promote``). Unset leaves
# the extra signal off, so promotion keeps its default performance/work rule.
DEFAULT_MIN_SITELINKS: int | None = settings.gold_min_sitelinks

# Rule 1 threshold: keep people and ensembles credited on at least this many
# concerts/recordings. Defaults to 1, i.e. one real appearance is enough.
DEFAULT_MIN_APPEARANCES: int = settings.gold_min_appearances

# Rule 3 threshold: keep entities referenced by at least this many distinct kept
# persons. Defaults to 1, i.e. keep anything referenced at all.
DEFAULT_MIN_REFERRERS: int = settings.gold_min_referrers

__all__ = [
    "DEFAULT_GOLD_DB_PATH",
    "DEFAULT_MIN_APPEARANCES",
    "DEFAULT_MIN_REFERRERS",
    "DEFAULT_MIN_SITELINKS",
    "GoldManifest",
    "PromoteConfig",
    "PromoteStats",
    "promote",
    "read_gold_manifest",
]
