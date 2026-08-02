"""Tests for the RCO HTTP fetch layer.

Retrying and client construction moved to :mod:`composer_http` and are tested
there; what stays RCO-specific is pulling concert slugs out of a calendar page.
"""

from __future__ import annotations

from composer_scrapers.rco.fetch import page_slugs


def test_page_slugs_extracts_concert_slugs() -> None:
    html = """
    <a href="/en/calendar/beethoven-symphony-5-2026-09-01/">Concert</a>
    <a href="/en/calendar/brahms-violin-concerto-2026-10-15/">Concert</a>
    """
    assert page_slugs(html) == [
        "beethoven-symphony-5-2026-09-01",
        "brahms-violin-concerto-2026-10-15",
    ]


def test_page_slugs_ignores_calendar_root_and_other_links() -> None:
    html = """
    <a href="/en/calendar/">All concerts</a>
    <a href="/en/orchestra/">Orchestra</a>
    <a href="/en/calendar/beethoven-2026-09-01/">Concert</a>
    """
    assert page_slugs(html) == ["beethoven-2026-09-01"]


def test_page_slugs_requires_date_suffix() -> None:
    html = '<a href="/en/calendar/no-date-here/">Not a concert slug</a>'
    assert page_slugs(html) == []
