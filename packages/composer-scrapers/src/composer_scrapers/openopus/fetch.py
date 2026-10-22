"""HTTP access to the Open Opus API.

The whole database ships as one JSON document (``/work/dump.json``, a few MB):
a ``composers`` list where each composer carries its ``works`` inline. Some
Open Opus endpoints wrap their payload in a ``status`` envelope, so the dump
is unwrapped defensively.

The dump is the whole source, so it is fetched fresh each run, never mirrored.
"""

from __future__ import annotations

import logging
from typing import Any

from composer_http import SourceSession

BASE_URL = "https://openopus.org"
DUMP_URL = "https://api.openopus.org/work/dump.json"

log = logging.getLogger(__name__)


#: The dump is a few MB in one response, so it gets a far longer timeout than
#: the per-request default.
TIMEOUT_S = 120.0


def _fetch_dump(session: SourceSession) -> list[dict[str, Any]]:
    """Download the full work dump and return its ``composers`` list."""
    data = session.get_json(DUMP_URL, label="work dump")
    composers = data.get("composers")
    if not isinstance(composers, list):
        raise ValueError("work dump has no 'composers' list")
    log.info("openopus: dump lists %d composers", len(composers))
    return composers
