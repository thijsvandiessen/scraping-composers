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


@pytest.fixture(autouse=True)
def claims_fit_the_ontology(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test whose adapter emits a claim the vocabulary does not list.

    Every adapter test parses real fixtures, so wrapping ``SourceClaim``'s
    constructor here checks each predicate the adapters actually produce,
    without the check running (and possibly rejecting old bronze) in
    production.
    """
    from composer_schema import SourceClaim
    from composer_schema.ontology import check_claim

    init = SourceClaim.__init__

    def checked_init(self: SourceClaim, *args: object, **kwargs: object) -> None:
        init(self, *args, **kwargs)  # pyright: ignore[reportArgumentType]
        check_claim(self)

    monkeypatch.setattr(SourceClaim, "__init__", checked_init)
