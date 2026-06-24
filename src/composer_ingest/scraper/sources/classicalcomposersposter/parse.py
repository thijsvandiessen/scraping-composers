"""Parse the Classical Composers Poster insert-sheet PDF into composer rows.

Uses Docling's layout-aware DocumentConverter for structured table extraction,
falling back to markdown text parsing when no tables are detected.

Each parsed row is a dict with keys ``"name"``, ``"born"`` and ``"died"``
(both optional strings).
"""

from __future__ import annotations

import logging
import re
from io import BytesIO
from typing import Any

from docling.datamodel.base_models import DocumentStream
from docling.document_converter import DocumentConverter

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
    if not _YEAR_RE.search(raw):
        return None
    return raw or None


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
    """Fallback: extract rows from plain text by matching year patterns per line."""
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
        rows.append({
            "name": name_part,
            "born": years[0] if years else None,
            "died": years[1] if len(years) > 1 else None,
        })
    return rows


def _rows_from_dataframe(
    df: Any, name_col: int, born_col: int | None, died_col: int | None
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, cells in df.iterrows():
        cell_list = [str(v) if v is not None else "" for v in cells]
        if len(cell_list) <= name_col:
            continue
        name = cell_list[name_col].strip()
        if not name or _looks_like_header([name]):
            continue
        born_raw = cell_list[born_col] if born_col is not None and born_col < len(cell_list) else None
        died_raw = cell_list[died_col] if died_col is not None and died_col < len(cell_list) else None
        rows.append({"name": name, "born": _clean_year(born_raw), "died": _clean_year(died_raw)})
    return rows


def _parse_rows(pdf_bytes: bytes, max_pages: int | None = None) -> list[dict[str, Any]]:
    """Extract composer rows from PDF bytes using Docling."""
    source = DocumentStream(name="composers.pdf", stream=BytesIO(pdf_bytes))
    converter = DocumentConverter()
    kwargs: dict[str, Any] = {}
    if max_pages is not None:
        kwargs["max_num_pages"] = max_pages
    result = converter.convert(source, **kwargs)

    rows: list[dict[str, Any]] = []
    for table in result.document.tables:
        df = table.export_to_dataframe()
        if df.empty:
            continue
        header = [str(v) if v is not None else None for v in df.iloc[0]]
        if _looks_like_header(header):
            name_col, born_col, died_col = _detect_columns(header)
            rows.extend(_rows_from_dataframe(df.iloc[1:], name_col, born_col, died_col))
        else:
            rows.extend(_rows_from_dataframe(df, 0, None, None))

    if not rows:
        rows = _parse_text_lines(result.document.export_to_markdown())

    return rows
