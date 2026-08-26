"""Shared fixtures for the scraper test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def scraper_contact_email(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scrapers refuse to build a User-Agent without SCRAPER_CONTACT_EMAIL; give tests one."""
    from composer_config import settings

    monkeypatch.setattr(settings, "scraper_contact_email", "test-contact@example.com")


@pytest.fixture(autouse=True)
def no_page_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never mirror pages to disk from a test: the default path is the repo root."""
    from composer_config import settings

    monkeypatch.setattr(settings, "page_cache_enabled", False)
