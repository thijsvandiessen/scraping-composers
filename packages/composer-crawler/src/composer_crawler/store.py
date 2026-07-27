"""File-backed storage for dashboard-managed crawl configurations.

Crawl configs created in the dashboard are durable data, not code: they live
in a single JSON file (``CRAWL_CONFIGS_PATH``, a sibling of the bucket) that
the admin API reads and writes. :func:`all_crawl_configs` merges them with the
code-registered :data:`~composer_crawler.registry.CRAWL_REGISTRY`; on a name
collision the code config wins, so a stored edit can never shadow a reviewed,
versioned one.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from composer_config import settings

from .config import CrawlConfig
from .registry import CRAWL_REGISTRY

DEFAULT_CRAWL_CONFIGS_PATH = settings.crawl_configs_path

STORE_VERSION = 1

log = logging.getLogger(__name__)

# Scalar CrawlConfig fields carried through the store verbatim (name/seeds/
# allow_patterns/headers need their own tuple handling and are excluded).
_SCALAR_FIELDS = (
    "use_sitemap",
    "use_common_crawl",
    "relevance_query",
    "score_threshold",
    "follow_links",
    "max_depth",
    "max_pages",
    "excluded_selector",
    "request_delay_s",
    "respect_robots",
    "timeout_s",
    "extract_kind",
)


def config_to_dict(config: CrawlConfig) -> dict[str, Any]:
    """Encode a config as JSON-ready primitives (tuples become lists)."""
    data: dict[str, Any] = {"name": config.name, "seeds": list(config.seeds)}
    for field in _SCALAR_FIELDS:
        data[field] = getattr(config, field)
    data["allow_patterns"] = list(config.allow_patterns)
    data["headers"] = [list(header) for header in config.headers]
    return data


def config_from_dict(data: dict[str, Any]) -> CrawlConfig:
    """Decode a config dict; ``CrawlConfig.__post_init__`` does the validating.

    Fields absent from the dict keep the dataclass defaults, so old files keep
    loading when new optional fields appear (and drop ones that go away).
    """
    kwargs: dict[str, Any] = {"name": data["name"], "seeds": tuple(data["seeds"])}
    for field in _SCALAR_FIELDS:
        if field in data:
            kwargs[field] = data[field]
    if "allow_patterns" in data:
        kwargs["allow_patterns"] = tuple(data["allow_patterns"])
    if "headers" in data:
        kwargs["headers"] = tuple((key, value) for key, value in data["headers"])
    return CrawlConfig(**kwargs)


@dataclass(frozen=True)
class CrawlConfigStore:
    """The crawl-configs JSON file: a versioned envelope of config dicts."""

    path: Path

    def load(self) -> dict[str, CrawlConfig]:
        """All stored configs by name; a missing file is an empty store."""
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            envelope = json.loads(raw)
            configs = [config_from_dict(item) for item in envelope["configs"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"invalid crawl configs file {self.path}: {exc}") from exc
        return {config.name: config for config in configs}

    def get(self, name: str) -> CrawlConfig | None:
        return self.load().get(name)

    def save(self, config: CrawlConfig) -> None:
        """Create or replace one config; the file is swapped in atomically."""
        configs = self.load()
        configs[config.name] = config
        self._write(configs)

    def delete(self, name: str) -> bool:
        """Remove a config; False when it wasn't stored."""
        configs = self.load()
        if name not in configs:
            return False
        del configs[name]
        self._write(configs)
        return True

    def _write(self, configs: dict[str, CrawlConfig]) -> None:
        envelope = {
            "version": STORE_VERSION,
            "configs": [config_to_dict(config) for _, config in sorted(configs.items())],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(self.path.name + ".tmp")
        tmp_path.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp_path, self.path)


def all_crawl_configs(path: str | Path | None = None) -> dict[str, CrawlConfig]:
    """Code-registered and stored configs merged; the code registry wins."""
    stored = CrawlConfigStore(Path(path or DEFAULT_CRAWL_CONFIGS_PATH)).load()
    for name in stored.keys() & CRAWL_REGISTRY.keys():
        log.warning("stored crawl config %r is shadowed by the code-registered one", name)
    return {**stored, **CRAWL_REGISTRY}
