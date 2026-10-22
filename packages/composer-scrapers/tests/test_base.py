"""Tests for the shape every source shares (:mod:`composer_scrapers.base`)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from composer_http import SourceSession
from composer_http.testing import mock_session
from composer_schema import EntityDocument, RefreshCadence
from composer_scrapers import REGISTRY, HttpSourceAdapter
from composer_scrapers.nyphil import NyPhilAdapter

#: The sources that hold a record of things already given: scraped once.
ARCHIVES = {"wienerphil", "roh", "concertgebouw_archive", "berlinphil", "nyphil"}


class _Probe(HttpSourceAdapter[EntityDocument]):
    name = "probe"
    base_url = "https://example.org"
    cadence = RefreshCadence.YEARLY
    request_delay_s = 2.5
    timeout_s = 77.0
    headers = {"Accept-Language": "en"}
    follow_redirects = True

    def scrape(self, session: SourceSession, max_pages: int | None = None) -> Iterator[EntityDocument]:
        session.get_text("https://example.org/", label="home")
        yield from ()


def test_the_session_carries_the_adapters_manners() -> None:
    with _Probe().open_session() as session:
        assert session.source == "probe"
        assert session.delay_s == 2.5
        assert session.client.timeout.read == 77.0
        assert session.client.follow_redirects is True
        assert session.client.headers["Accept-Language"] == "en"
        assert "test-contact@example.com" in session.client.headers["User-Agent"]


def test_the_mirror_lives_as_long_as_the_cadence() -> None:
    with _Probe().open_session() as session:
        assert session.max_age == timedelta(days=365)


def test_an_archive_keeps_its_mirror_forever() -> None:
    class _Archive(_Probe):
        cadence = RefreshCadence.STATIC

    with _Archive().open_session() as session:
        assert session.max_age is None


def test_the_mirror_follows_the_setting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from composer_config import settings

    monkeypatch.setattr(settings, "page_cache_enabled", True)
    monkeypatch.setattr(settings, "page_cache_path", str(tmp_path / "pages.db"))
    with _Probe().open_session() as session:
        assert session.cache is not None
        assert session.cache.path == tmp_path / "pages.db"


def test_fetch_runs_scrape_inside_one_session(monkeypatch: pytest.MonkeyPatch) -> None:
    session = mock_session(lambda _request: httpx.Response(200, text="ok"))
    monkeypatch.setattr(_Probe, "open_session", lambda self: session)
    assert list(_Probe().fetch()) == []
    assert session.requests == 1
    assert session.client.is_closed


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_source_has_the_same_shape(name: str) -> None:
    """One architecture: every HTTP source is an HttpSourceAdapter. nyphil is the
    exception that proves it — a kagglehub download, not HTTP."""
    adapter = REGISTRY[name]
    if name == "nyphil":
        assert isinstance(adapter, NyPhilAdapter)
        assert not isinstance(adapter, HttpSourceAdapter)
    else:
        assert isinstance(adapter, HttpSourceAdapter)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_archives_are_scraped_once_and_everything_else_yearly(name: str) -> None:
    expected = RefreshCadence.STATIC if name in ARCHIVES else RefreshCadence.YEARLY
    assert REGISTRY[name].cadence is expected
