"""HTTP access to the Open Opus API.

The whole database ships as one JSON document (``/work/dump.json``, a few MB):
a ``composers`` list where each composer carries its ``works`` inline. Some
Open Opus endpoints wrap their payload in a ``status`` envelope, so the dump
is unwrapped defensively.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .._http import call_with_retries, user_agent

BASE_URL = "https://openopus.org"
DUMP_URL = "https://api.openopus.org/work/dump.json"
RETRIES = 3

log = logging.getLogger(__name__)


def _make_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": user_agent()}, timeout=120)


def _fetch_dump(client: httpx.Client) -> list[dict[str, Any]]:
    """Download the full work dump and return its ``composers`` list."""

    def do() -> dict[str, Any]:
        resp = client.get(DUMP_URL)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    data = call_with_retries(do, label="work dump", retries=RETRIES, retry_on=(httpx.HTTPError, ValueError))
    composers = data.get("composers")
    if not isinstance(composers, list):
        raise ValueError("work dump has no 'composers' list")
    log.info("openopus: dump lists %d composers", len(composers))
    return composers
