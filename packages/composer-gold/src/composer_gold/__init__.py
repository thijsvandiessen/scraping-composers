"""Gold layer: promote the silver staging database into a curated copy.

Silver records what sources say plus the matching passes over it. ``promote``
rebuilds the gold database from it, applying the curation rules research
consumers want:

1. drop people and ensembles below the concert/recording thresholds configured
   in ``rule1_config.json`` (see ``Rule1Config``; read/writable live through the
   admin API's ``/admin/v1/rule1-config``), unless a person clears the
   separately configurable composer-credit threshold,
2. collapse duplicate person entities into their canonical row,
3. prune entities referenced by fewer than ``min_referrers`` distinct kept
   persons (default 1: keep anything referenced at all).
"""

from composer_config import settings

from ._rule1_config import DEFAULT_RULE1_CONFIG_PATH, EnsembleRule1Config, PersonRule1Config, Rule1Config
from .promote import GoldManifest, PromoteConfig, PromoteStats, promote, read_gold_manifest

DEFAULT_GOLD_DB_PATH = settings.gold_db_path

# Rule 3 threshold: keep entities referenced by at least this many distinct kept
# persons. Defaults to 1, i.e. keep anything referenced at all.
DEFAULT_MIN_REFERRERS: int = settings.gold_min_referrers

__all__ = [
    "DEFAULT_GOLD_DB_PATH",
    "DEFAULT_MIN_REFERRERS",
    "DEFAULT_RULE1_CONFIG_PATH",
    "EnsembleRule1Config",
    "GoldManifest",
    "PersonRule1Config",
    "PromoteConfig",
    "PromoteStats",
    "Rule1Config",
    "promote",
    "read_gold_manifest",
]
