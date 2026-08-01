"""Shared fixtures for the HTTP helper test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def scraper_contact_email(monkeypatch: pytest.MonkeyPatch) -> None:
    """The helpers refuse to build a User-Agent without SCRAPER_CONTACT_EMAIL; give tests one."""
    from composer_config import settings

    monkeypatch.setattr(settings, "scraper_contact_email", "test-contact@example.com")
