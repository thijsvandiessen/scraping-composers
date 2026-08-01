"""Reusing what the model already answered, and knowing when not to.

A crawl rewrites its whole snapshot every run, so re-extracting one used to re-ask
the model about text it had already read. These tests pin both halves of the fix:
an identical request is served from the cache, and every input that could change
the answer — the page, its metadata, the prompt, the model, the schema — misses.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from composer_extract import ExtractCache, OllamaExtractor, open_cache
from composer_extract import client as client_mod

_EMPTY = '{"concerts": []}'
_ONE_CONCERT = '{"concerts": [{"date": "2024-05-01", "venue": "Barbican", "conductors": [], "soloists": [], "works": [{"title": "Eroica", "composer": "Beethoven"}]}]}'  # noqa: E501


class CountingChat:
    """A fake ``ollama.Client.chat`` that records how often it was called."""

    def __init__(self, content: str = _EMPTY) -> None:
        self.content = content
        self.calls = 0

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        return {"message": {"content": self.content}}


def _extractor(cache: ExtractCache | None, chat: CountingChat, model: str = "qwen2.5") -> OllamaExtractor:
    return OllamaExtractor(model=model, chat=chat).with_cache(cache)


@pytest.fixture(name="cache")
def cache_fixture(tmp_path: Path) -> ExtractCache:
    return ExtractCache(tmp_path / "extract-cache.db")


def test_a_page_the_model_already_read_is_not_sent_again(cache: ExtractCache) -> None:
    """The whole point: the second extract of unchanged text costs no model call."""
    chat = CountingChat(_ONE_CONCERT)
    extractor = _extractor(cache, chat)

    first = extractor.extract_page("# Concert", {"title": "Eroica"})
    second = _extractor(cache, chat).extract_page("# Concert", {"title": "Eroica"})

    assert chat.calls == 1
    assert second == first
    assert second.concerts[0].works[0].title == "Eroica"


def test_a_page_with_nothing_on_it_is_cached_too(cache: ExtractCache) -> None:
    """Most crawled pages hold no concert. If empty answers were not cached,
    exactly those pages would be re-analysed on every single run."""
    chat = CountingChat(_EMPTY)

    _extractor(cache, chat).extract_page("# About us", {})
    _extractor(cache, chat).extract_page("# About us", {})

    assert chat.calls == 1


def test_an_unusable_answer_is_never_cached(cache: ExtractCache) -> None:
    """Truncated JSON is what .resilience retries on; caching it would make a
    transient model failure permanent."""
    chat = CountingChat('{"concerts": [{"date": "2024-05-01", "soloists": [{"name": "X"')
    extractor = _extractor(cache, chat)

    with pytest.raises(ValueError):
        extractor.extract_page("# Concert", {})

    chat.content = _EMPTY
    assert extractor.extract_page("# Concert", {}).concerts == []
    assert chat.calls == 2


def test_editing_the_system_prompt_re_asks_the_model(
    cache: ExtractCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression guard for the worst failure mode: tuning the prompt and
    seeing no change because every page came back from the cache."""
    chat = CountingChat()
    _extractor(cache, chat).extract_page("# Concert", {})

    monkeypatch.setattr(client_mod, "SYSTEM_PROMPT", "A better prompt.")
    _extractor(cache, chat).extract_page("# Concert", {})

    assert chat.calls == 2


def test_another_model_does_not_inherit_this_ones_answers(cache: ExtractCache) -> None:
    chat = CountingChat()
    _extractor(cache, chat, model="qwen2.5").extract_page("# Concert", {})
    _extractor(cache, chat, model="llama3.1").extract_page("# Concert", {})

    assert chat.calls == 2


def test_page_metadata_is_part_of_the_key(cache: ExtractCache) -> None:
    """Metadata is folded into the prompt by build_user_prompt, so a markdown-only
    hash would serve the wrong answer for two pages sharing a body."""
    chat = CountingChat()
    _extractor(cache, chat).extract_page("# Programme", {"title": "May concert"})
    _extractor(cache, chat).extract_page("# Programme", {"title": "June concert"})

    assert chat.calls == 2


def test_changed_markdown_re_asks_the_model(cache: ExtractCache) -> None:
    chat = CountingChat()
    _extractor(cache, chat).extract_page("# Concert", {})
    _extractor(cache, chat).extract_page("# Concert (cancelled)", {})

    assert chat.calls == 2


def test_the_two_extraction_modes_do_not_share_entries(cache: ExtractCache) -> None:
    """Same page, different question: concerts and recordings ask for different
    schemas and must not answer for each other."""
    chat = CountingChat()
    _extractor(cache, chat).extract_page("# Album", {})
    chat.content = '{"recordings": []}'
    _extractor(cache, chat).extract_recording_page("# Album", {})

    assert chat.calls == 2


def test_without_a_cache_every_page_goes_to_the_model() -> None:
    chat = CountingChat()
    extractor = _extractor(None, chat)

    extractor.extract_page("# Concert", {})
    extractor.extract_page("# Concert", {})

    assert chat.calls == 2


def test_a_damaged_row_is_dropped_rather_than_raised(cache: ExtractCache) -> None:
    """One corrupt entry should cost a single model call, not fail the page."""
    chat = CountingChat()
    _extractor(cache, chat).extract_page("# Concert", {})
    with sqlite3.connect(cache.path) as connection:
        connection.execute("UPDATE extraction_cache SET response = 'not json'")

    assert _extractor(cache, chat).extract_page("# Concert", {}).concerts == []
    assert chat.calls == 2

    _extractor(cache, chat).extract_page("# Concert", {})
    assert chat.calls == 2, "the repaired entry should now serve from cache"


def test_a_missing_cache_file_is_created_on_first_use(tmp_path: Path) -> None:
    cache = ExtractCache(tmp_path / "nested" / "extract-cache.db")
    _extractor(cache, CountingChat()).extract_page("# Concert", {})

    assert cache.path.exists()


def test_the_summary_reports_what_was_saved(cache: ExtractCache) -> None:
    chat = CountingChat()
    _extractor(cache, chat).extract_page("# A", {})
    _extractor(cache, chat).extract_page("# A", {})

    assert cache.hits == 1
    assert cache.misses == 1
    assert "50% of calls saved" in cache.summary()


def test_open_cache_returns_nothing_when_switched_off(tmp_path: Path) -> None:
    assert open_cache(tmp_path / "c.db", enabled=False) is None
    assert open_cache(tmp_path / "c.db", enabled=True) is not None
