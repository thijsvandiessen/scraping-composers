"""Contact identity advertised while crawling.

crawl4ai handles the actual fetching, retries and rate limiting; all this module
still owns is the polite User-Agent that names a reachable contact, mirroring
``composer_scrapers._http``.
"""

from __future__ import annotations


def contact_email() -> str:
    """Contact email advertised in User-Agent headers.

    Polite crawling means the crawled sites can reach whoever runs the
    crawler, so ``SCRAPER_CONTACT_EMAIL`` must be set — there is no default.
    Read at call time, not import time, so the environment can be set after
    the module is imported.
    """
    from composer_config import settings

    email = settings.scraper_contact_email
    if not email:
        raise RuntimeError(
            "SCRAPER_CONTACT_EMAIL is not set; crawlers must advertise a reachable contact email"
        )
    return email


def user_agent() -> str:
    """User-Agent for the headless browser and the URL seeder."""
    return f"composer-ingest/0.1 (research; {contact_email()})"
