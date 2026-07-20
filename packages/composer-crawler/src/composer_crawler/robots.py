"""robots.txt compliance: fetch once per host, fail open when unavailable."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

# Product token matched against robots.txt User-agent lines.
AGENT_TOKEN = "composer-ingest"

log = logging.getLogger(__name__)


class RobotsCache:
    """Per-host robots.txt decisions, fetched lazily through the crawl's client.

    An unreachable or non-2xx robots.txt allows everything (RFC 9309 treats
    4xx as unrestricted; treating errors as allow keeps the crawler from
    stalling on hosts without one).
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self._parsers: dict[str, RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._parsers:
            self._parsers[host] = self._fetch(host)
        parser = self._parsers[host]
        return parser is None or parser.can_fetch(AGENT_TOKEN, url)

    def _fetch(self, host: str) -> RobotFileParser | None:
        robots_url = f"{host}/robots.txt"
        try:
            response = self._client.get(robots_url)
        except httpx.HTTPError as exc:
            log.info("robots.txt unreachable at %s (%s); allowing all", robots_url, exc)
            return None
        if response.status_code != 200:
            return None
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser
