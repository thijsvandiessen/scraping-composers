import json
from pathlib import Path

import pytest
from composer_crawler import (
    CrawlConfig,
    CrawlConfigStore,
    all_crawl_configs,
    config_from_dict,
    config_to_dict,
)


def full_config() -> CrawlConfig:
    return CrawlConfig(
        name="example",
        seeds=("https://example.org/a", "https://example.org/b"),
        use_sitemap=True,
        use_common_crawl=True,
        follow_links=True,
        allow_patterns=("*/composer/*",),
        relevance_query="composer biography works",
        score_threshold=0.3,
        max_depth=3,
        max_pages=50,
        request_delay_s=1.5,
        headers=(("Accept", "application/json"),),
        respect_robots=False,
        timeout_s=10.0,
    )


@pytest.mark.parametrize(
    "config",
    [
        full_config(),
        CrawlConfig(name="api", seeds=("https://api.example.org/v1",), use_common_crawl=True),
        CrawlConfig(name="plain", seeds=("https://example.org/",)),
    ],
)
def test_round_trip(config: CrawlConfig) -> None:
    assert config_from_dict(config_to_dict(config)) == config


def test_from_dict_defaults_missing_fields() -> None:
    config = config_from_dict({"name": "example", "seeds": ["https://example.org/"]})
    assert config == CrawlConfig(name="example", seeds=("https://example.org/",))


def test_from_dict_ignores_legacy_pagination_key() -> None:
    # Pre-crawl4ai configs carried a `pagination` field; it must load, not raise.
    config = config_from_dict(
        {"name": "example", "seeds": ["https://example.org/"], "pagination": {"type": "page_param"}}
    )
    assert config == CrawlConfig(name="example", seeds=("https://example.org/",))


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    assert CrawlConfigStore(tmp_path / "crawl_configs.json").load() == {}


def test_save_get_delete_cycle(tmp_path: Path) -> None:
    store = CrawlConfigStore(tmp_path / "crawl_configs.json")
    config = full_config()
    store.save(config)
    other = CrawlConfig(name="other", seeds=("https://other.example/",))
    store.save(other)

    assert store.get("example") == config
    assert set(store.load()) == {"example", "other"}

    updated = CrawlConfig(name="example", seeds=("https://example.org/new",))
    store.save(updated)
    assert store.get("example") == updated

    assert store.delete("example") is True
    assert store.delete("example") is False
    assert set(store.load()) == {"other"}


def test_load_corrupt_file_names_path(tmp_path: Path) -> None:
    path = tmp_path / "crawl_configs.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="crawl_configs.json"):
        CrawlConfigStore(path).load()

    path.write_text(json.dumps({"version": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="crawl_configs.json"):
        CrawlConfigStore(path).load()


def test_all_crawl_configs_code_registry_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from composer_crawler.registry import CRAWL_REGISTRY

    code_config = CrawlConfig(name="example", seeds=("https://code.example/",))
    monkeypatch.setitem(CRAWL_REGISTRY, "example", code_config)

    store = CrawlConfigStore(tmp_path / "crawl_configs.json")
    store.save(CrawlConfig(name="example", seeds=("https://stored.example/",)))
    store.save(CrawlConfig(name="stored-only", seeds=("https://stored.example/",)))

    merged = all_crawl_configs(store.path)
    assert merged["example"] == code_config
    assert "stored-only" in merged
