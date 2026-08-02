"""Shared PDF fetch utility and base adapter for PDF-based sources.

Any source that consists of a single downloadable PDF should subclass
:class:`PdfSourceAdapter` and define ``name``, ``base_url``, and ``pdf_url``
as class-level constants.  Call ``self._download_pdf()`` inside ``fetch()``
to retrieve the raw bytes.
"""

from __future__ import annotations

from typing import ClassVar

import httpx
from composer_http import browser_user_agent, call_with_retries

from . import SourceAdapter


def _fetch_pdf_bytes(client: httpx.Client, url: str) -> bytes:
    """GET *url* and return the response body, retrying.

    Not :func:`composer_http.get_text` or ``get_json``: a PDF is bytes, and
    decoding it as either would corrupt it.
    """

    def do() -> bytes:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content

    return call_with_retries(do, label="PDF fetch")


class PdfSourceAdapter(SourceAdapter):
    """Base adapter for sources that consist of a single downloadable PDF.

    Subclasses must declare ``pdf_url`` in addition to the ``name`` and
    ``base_url`` required by :class:`SourceAdapter`.
    """

    pdf_url: ClassVar[str]

    def _download_pdf(self) -> bytes:
        """Download :attr:`pdf_url` with browser-style headers and return raw bytes."""
        with httpx.Client(
            headers={"User-Agent": browser_user_agent(), "Referer": self.base_url},
            timeout=60,
            follow_redirects=True,
        ) as client:
            return _fetch_pdf_bytes(client, self.pdf_url)
