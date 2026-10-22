"""IMSLP worklist API access.

The API is awkward: a single GET endpoint whose "query string" is a
slash-separated path inside one parameter, returning a JSON object keyed by
stringified row indices ("0".."999") plus a "metadata" entry that carries the
pagination flag.

It serves two lists, and IMSLP's own documentation (``/wiki/IMSLP:API``)
describes no others: ``type=1`` is the people (composers, performers, editors),
which this package reads, and ``type=2`` is the ~267,000 works, which
:mod:`composer_scrapers.imslp_works` reads. Both come back in the shape above,
so :func:`worklist_page` is shared rather than written twice.
"""

from __future__ import annotations

from typing import Any

from composer_http import SourceSession

BASE_URL = "https://imslp.org"

API_URL = BASE_URL + "/imslpscripts/API.ISCR.php"
PAGE_SIZE = 1000  # fixed by the API
REQUEST_DELAY_S = 1.0


#: The two lists the endpoint serves.
PEOPLE = 1
WORKS = 2


def worklist_url(start: int, kind: int) -> str:
    """One page of the worklist. The API expects its parameters as a single
    slash-separated string; encoding them as separate query params breaks it."""
    return f"{API_URL}?account=worklist/disclaimer=accepted/sort=id/type={kind}/start={start}/retformat=json"


def worklist_page(session: SourceSession, start: int, kind: int = PEOPLE) -> dict[str, Any]:
    """One page of rows, still keyed by stringified index, with ``metadata``.

    Not mirrored: the worklist is the enumerator, so a re-run must see it as it
    is now rather than as it was.
    """
    return session.get_json(worklist_url(start, kind), label=f"type={kind} start={start}")
