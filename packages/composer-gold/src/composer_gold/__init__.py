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

``kumu`` then exports a slice of the promoted database as a Kumu blueprint,
for mapping the performer/composer network visually.
"""

from composer_config import settings

from ._rule1_config import DEFAULT_RULE1_CONFIG_PATH, EnsembleRule1Config, PersonRule1Config, Rule1Config
from .kumu import (
    DEFAULT_PERFORMER_LIMIT,
    Blueprint,
    ExportStats,
    KumuConfig,
    build_blueprint,
    export_kumu,
)
from .promote import GoldManifest, PromoteConfig, PromoteStats, promote, read_gold_manifest

DEFAULT_GOLD_DB_PATH = settings.gold_db_path

# Rule 3 threshold: keep entities referenced by at least this many distinct kept
# persons. Defaults to 1, i.e. keep anything referenced at all.
DEFAULT_MIN_REFERRERS: int = settings.gold_min_referrers

__all__ = [
    "DEFAULT_GOLD_DB_PATH",
    "DEFAULT_MIN_REFERRERS",
    "DEFAULT_PERFORMER_LIMIT",
    "DEFAULT_RULE1_CONFIG_PATH",
    "Blueprint",
    "EnsembleRule1Config",
    "ExportStats",
    "GoldManifest",
    "KumuConfig",
    "PersonRule1Config",
    "PromoteConfig",
    "PromoteStats",
    "Rule1Config",
    "build_blueprint",
    "export_kumu",
    "promote",
    "read_gold_manifest",
]
