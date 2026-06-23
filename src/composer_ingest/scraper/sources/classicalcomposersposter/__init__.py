"""Classical Composers Poster insert-sheet PDF source.

Downloads http://www.classicalcomposersposter.com/insert_sheet3.1.pdf and
yields one EntityDocument per composer row.  Birth and death years are
attached as ``born_on`` / ``died_on`` claims when present in the sheet.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx

from .. import EntityDocument, SourceAdapter, SourceClaim
from .fetch import BASE_URL, _fetch_pdf
from .parse import _parse_rows

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "ClassicalComposersPosterAdapter"]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    return _SLUG_RE.sub("-", name.lower()).strip("-")


class ClassicalComposersPosterAdapter(SourceAdapter):
    name = "classicalcomposersposter"
    base_url = BASE_URL

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument]:
        """Yield every composer listed in the insert-sheet PDF."""
        _ua = "Mozilla/5.0 (compatible; composer-ingest/0.1; research; thijsvandiessen@gmail.com)"
        with httpx.Client(
            headers={"User-Agent": _ua, "Referer": BASE_URL},
            timeout=60,
            follow_redirects=True,
        ) as client:
            pdf_bytes = _fetch_pdf(client)

        rows = _parse_rows(pdf_bytes, max_pages=max_pages)
        log.info("classicalcomposersposter: parsed %d rows", len(rows))

        ingested_at = datetime.now(UTC)
        for row in rows:
            name: str = row["name"]
            if not name:
                continue
            claims: list[SourceClaim] = []
            if row.get("born"):
                claims.append(SourceClaim("born_on", value=row["born"]))
            if row.get("died"):
                claims.append(SourceClaim("died_on", value=row["died"]))
            yield EntityDocument(
                id=f"classicalcomposersposter:{_slugify(name)}",
                url=BASE_URL,
                source_name=self.name,
                ingested_at=ingested_at,
                name=name,
                kind="person",
                raw=row,
                claims=tuple(claims),
            )
