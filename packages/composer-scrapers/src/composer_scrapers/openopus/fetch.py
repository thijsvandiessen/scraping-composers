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
from composer_http import get_json, new_client

BASE_URL = "https://openopus.org"
DUMP_URL = "https://api.openopus.org/work/dump.json"

log = logging.getLogger(__name__)


def _make_client() -> httpx.Client:
    # The dump is a few MB in one response, so it gets a far longer timeout
    # than the per-request default.
    return new_client(timeout=120)


def _fetch_dump(client: httpx.Client) -> list[dict[str, Any]]:
    """Download the full work dump and return its ``composers`` list."""
    data = get_json(client, DUMP_URL, label="work dump")
    composers = data.get("composers")
    if not isinstance(composers, list):
        raise ValueError("work dump has no 'composers' list")
    log.info("openopus: dump lists %d composers", len(composers))
    return composers
