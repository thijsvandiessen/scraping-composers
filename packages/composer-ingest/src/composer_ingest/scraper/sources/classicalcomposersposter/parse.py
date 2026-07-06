"""Parse the Classical Composers Poster insert-sheet PDF into composer rows.

Uses Docling's layout-aware DocumentConverter for structured table extraction,
falling back to markdown text parsing when no tables are detected.

Each parsed row is a dict with keys ``"name"``, ``"born"`` and ``"died"``
(both optional strings).
"""

from __future__ import annotations

import logging
import math
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
        rows.append(
            {
                "name": name_part,
                "born": years[0] if years else None,
                "died": years[1] if len(years) > 1 else None,
            }
        )
    return rows


def _cell_str(v: Any) -> str:
    """Convert a pandas cell value to a clean string, treating NaN as empty."""
    if v is None:
        return ""
    try:
        if math.isnan(float(v)):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v)


def _rows_from_dataframe(
    df: Any, name_col: int, born_col: int | None, died_col: int | None
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, cells in df.iterrows():
        cell_list = [_cell_str(v) for v in cells]
        if len(cell_list) <= name_col:
            continue
        name = cell_list[name_col].strip()
        if not name or _looks_like_header([name]):
            continue
        born_raw = cell_list[born_col] if born_col is not None and born_col < len(cell_list) else None
        died_raw = cell_list[died_col] if died_col is not None and died_col < len(cell_list) else None
        rows.append({"name": name, "born": _clean_year(born_raw), "died": _clean_year(died_raw)})
    return rows


def _infer_date_columns(df: Any) -> tuple[int | None, int | None]:
    """Scan all columns and return (born_col, died_col) based on year-density heuristic.

    A column is a candidate date column when the majority of its non-empty cells
    contain a year-like value.  The first such column is treated as born, the
    second as died.
    """
    n_rows = len(df)
    if n_rows == 0:
        return None, None
    date_cols: list[int] = []
    for col_idx in range(len(df.columns)):
        col_vals = [_cell_str(v) for v in df.iloc[:, col_idx]]
        year_hits = sum(1 for v in col_vals if v and _YEAR_RE.search(v))
        if year_hits >= max(1, n_rows // 2):
            date_cols.append(col_idx)
    born_col = date_cols[0] if len(date_cols) > 0 else None
    died_col = date_cols[1] if len(date_cols) > 1 else None
    return born_col, died_col


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
        header = [_cell_str(v) or None for v in df.iloc[0]]
        if _looks_like_header(header):
            name_col, born_col, died_col = _detect_columns(header)
            rows.extend(_rows_from_dataframe(df.iloc[1:], name_col, born_col, died_col))
        else:
            born_col, died_col = _infer_date_columns(df)
            rows.extend(_rows_from_dataframe(df, 0, born_col, died_col))

    if not rows:
        log.warning(
            "classicalcomposersposter: no structured tables found, falling back to markdown text parsing"
        )
        rows = _parse_text_lines(result.document.export_to_markdown())

    return rows
