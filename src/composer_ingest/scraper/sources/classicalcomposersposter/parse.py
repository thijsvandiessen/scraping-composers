"""Parse the Classical Composers Poster insert-sheet PDF into composer rows.

The PDF is an alphabetical table with at minimum a name column and birth/death
year columns (which may be labelled "Born", "Died", "b.", "d.", or similar).
Each parsed row is a dict with keys ``"name"``, ``"born"`` and ``"died"``
(both optional strings).
"""

from __future__ import annotations

import io
import logging
import re
from typing import Any

import pdfplumber

log = logging.getLogger(__name__)

_YEAR_RE = re.compile(r"\b(c\.?\s*)?\d{3,4}\b")
_HEADER_WORDS = {"name", "born", "died", "composer", "birth", "death", "b.", "d."}


def _looks_like_header(row: list[str | None]) -> bool:
    cells = [str(c).strip().lower() for c in row if c]
    return bool(cells and any(w in cells for w in _HEADER_WORDS))


def _clean_year(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    m = _YEAR_RE.search(raw)
    if not m:
        return None
    return raw.strip() or None


def _rows_from_table(
    table: list[list[str | None]], name_col: int, born_col: int | None, died_col: int | None
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cells in table:
        if len(cells) <= name_col:
            continue
        name = (cells[name_col] or "").strip()
        if not name or _looks_like_header([name]):
            continue
        born = _clean_year(cells[born_col] if born_col is not None and born_col < len(cells) else None)
        died = _clean_year(cells[died_col] if died_col is not None and died_col < len(cells) else None)
        rows.append({"name": name, "born": born, "died": died})
    return rows


def _detect_columns(header: list[str | None]) -> tuple[int, int | None, int | None]:
    """Return (name_col, born_col, died_col) indices from a header row."""
    name_col = 0
    born_col: int | None = None
    died_col: int | None = None
    for i, cell in enumerate(header):
        label = (cell or "").strip().lower()
        if label in {"born", "birth", "b.", "b"}:
            born_col = i
        elif label in {"died", "death", "d.", "d"}:
            died_col = i
        elif label in {"name", "composer"} and i == 0:
            name_col = i
    return name_col, born_col, died_col


def _parse_text_lines(text: str) -> list[dict[str, Any]]:
    """Fallback: parse raw text lines into rows."""
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        years = [m.group(0) for m in _YEAR_RE.finditer(line)]
        name_part = _YEAR_RE.sub("", line).strip().strip("-– ").strip()
        if not name_part or len(name_part) < 2:
            continue
        if _looks_like_header([name_part]):
            continue
        born = years[0] if len(years) > 0 else None
        died = years[1] if len(years) > 1 else None
        rows.append({"name": name_part, "born": born, "died": died})
    return rows


def _parse_rows(pdf_bytes: bytes, max_pages: int | None = None) -> list[dict[str, Any]]:
    """Extract composer rows from PDF bytes."""
    results: list[dict[str, Any]] = []
    name_col = 0
    born_col: int | None = None
    died_col: int | None = None
    header_detected = False

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = pdf.pages if max_pages is None else pdf.pages[:max_pages]
        for page in pages:
            table = page.extract_table()
            if not table:
                results.extend(_parse_text_lines(page.extract_text() or ""))
                continue
            if not header_detected and table[0] and _looks_like_header(table[0]):
                name_col, born_col, died_col = _detect_columns(table[0])
                header_detected = True
                data_rows = table[1:]
            else:
                data_rows = table
            results.extend(_rows_from_table(data_rows, name_col, born_col, died_col))

    return results
