import pytest
from composer_crawler import CrawlConfig


def test_valid_config() -> None:
    config = CrawlConfig(name="example", seeds=("https://example.org/",))
    assert config.max_depth == 2
    assert config.respect_robots is True
    assert config.use_sitemap is True
    assert config.use_common_crawl is False
    assert config.relevance_query is None
    assert config.score_threshold == 0.0


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "..\\x", "a\x00b", "../etc"])
def test_rejects_non_segment_names(name: str) -> None:
    with pytest.raises(ValueError, match="single path segment"):
        CrawlConfig(name=name, seeds=("https://example.org/",))


@pytest.mark.parametrize(
    "name", ["evil\nINFO forged entry", "evil\r\nINFO forged", "a\tb", "a\x1b[31mb", "a\x7fb"]
)
def test_rejects_names_carrying_control_characters(name: str) -> None:
    """A crawl name becomes a bucket directory and is then echoed into log lines.
    A newline in it is enough to forge a log entry, and ESC to start a terminal
    escape sequence, so the name never gets to hold one (CWE-117).
    """
    with pytest.raises(ValueError, match="single path segment"):
        CrawlConfig(name=name, seeds=("https://example.org/",))


@pytest.mark.parametrize(
    "name", ["lso", "concertgebouw_archive", "classicalcomposersposter", "a-b.c", "Source1"]
)
def test_accepts_the_names_actually_in_use(name: str) -> None:
    """The guard tightened to an allowlist; every name already on disk must survive it."""
    assert CrawlConfig(name=name, seeds=("https://example.org/",)).name == name


def test_rejects_empty_seeds() -> None:
    with pytest.raises(ValueError, match="seeds"):
        CrawlConfig(name="example", seeds=())


def test_follow_links_requires_allow_patterns() -> None:
    with pytest.raises(ValueError, match="allow pattern"):
        CrawlConfig(name="example", seeds=("https://example.org/",), follow_links=True)


def test_rejects_empty_allow_pattern() -> None:
    with pytest.raises(ValueError, match="non-empty globs"):
        CrawlConfig(
            name="example",
            seeds=("https://example.org/",),
            follow_links=True,
            allow_patterns=("",),
        )


def test_follow_links_with_patterns_is_valid() -> None:
    config = CrawlConfig(
        name="example",
        seeds=("https://example.org/",),
        follow_links=True,
        allow_patterns=("*/composer/*",),
    )
    assert config.follow_links
