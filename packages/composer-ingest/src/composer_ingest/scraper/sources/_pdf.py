"""Shared PDF fetch utility and base adapter for PDF-based sources.

Any source that consists of a single downloadable PDF should subclass
:class:`PdfSourceAdapter` and define ``name``, ``base_url``, and ``pdf_url``
as class-level constants.  Call ``self._download_pdf()`` inside ``fetch()``
to retrieve the raw bytes.
"""

from __future__ import annotations

import logging
import time
from typing import ClassVar

import httpx

from . import SourceAdapter

_UA = "Mozilla/5.0 (compatible; composer-ingest/0.1; research; thijsvandiessen@gmail.com)"
_RETRIES = 3

log = logging.getLogger(__name__)


def _fetch_pdf_bytes(client: httpx.Client, url: str) -> bytes:
    """GET *url* and return the response body, retrying up to ``_RETRIES`` times."""
    for attempt in range(1, _RETRIES + 1):
        try:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPError as exc:
            if attempt == _RETRIES:
                raise
            wait = 2**attempt
            log.warning("PDF fetch failed (%s), retrying in %ds", exc, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


class PdfSourceAdapter(SourceAdapter):
    """Base adapter for sources that consist of a single downloadable PDF.

    Subclasses must declare ``pdf_url`` in addition to the ``name`` and
    ``base_url`` required by :class:`SourceAdapter`.
    """

    pdf_url: ClassVar[str]

    def _download_pdf(self) -> bytes:
        """Download :attr:`pdf_url` with browser-style headers and return raw bytes."""
        with httpx.Client(
            headers={"User-Agent": _UA, "Referer": self.base_url},
            timeout=60,
            follow_redirects=True,
        ) as client:
            return _fetch_pdf_bytes(client, self.pdf_url)
