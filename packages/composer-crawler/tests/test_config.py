import pytest
from composer_crawler import CrawlConfig


def test_valid_config() -> None:
    config = CrawlConfig(name="example", seeds=("https://example.org/",))
    assert config.max_depth == 2
    assert config.respect_robots is True


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "..\\x", "a\x00b", "../etc"])
def test_rejects_non_segment_names(name: str) -> None:
    with pytest.raises(ValueError, match="single path segment"):
        CrawlConfig(name=name, seeds=("https://example.org/",))


def test_rejects_empty_seeds() -> None:
    with pytest.raises(ValueError, match="seeds"):
        CrawlConfig(name="example", seeds=())


def test_follow_links_requires_allow_patterns() -> None:
    with pytest.raises(ValueError, match="allow pattern"):
        CrawlConfig(name="example", seeds=("https://example.org/",), follow_links=True)


def test_follow_links_with_patterns_is_valid() -> None:
    config = CrawlConfig(
        name="example",
        seeds=("https://example.org/",),
        follow_links=True,
        allow_patterns=(r"example\.org",),
    )
    assert config.follow_links
