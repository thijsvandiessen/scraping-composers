"""HTTP fetch for the Classical Composers Poster insert sheet PDF."""

from __future__ import annotations

import logging
import time

import httpx

BASE_URL = "http://www.classicalcomposersposter.com"
PDF_URL = BASE_URL + "/insert_sheet3.1.pdf"

RETRIES = 3

log = logging.getLogger(__name__)


def _fetch_pdf(client: httpx.Client) -> bytes:
    """Download the insert-sheet PDF and return its raw bytes."""
    for attempt in range(1, RETRIES + 1):
        try:
            resp = client.get(PDF_URL)
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPError as exc:
            if attempt == RETRIES:
                raise
            wait = 2**attempt
            log.warning("PDF fetch failed (%s), retrying in %ds", exc, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")
