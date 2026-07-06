"""Tests for the Classical Composers Poster PDF source."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pandas as pd
import pytest
from composer_ingest.scraper.sources._pdf import PdfSourceAdapter, _fetch_pdf_bytes
from composer_ingest.scraper.sources.classicalcomposersposter import ClassicalComposersPosterAdapter
from composer_ingest.scraper.sources.classicalcomposersposter.parse import _infer_date_columns, _parse_rows

_DC_PATH = "composer_ingest.scraper.sources.classicalcomposersposter.parse.DocumentConverter"

# ---------------------------------------------------------------------------
# _fetch_pdf_bytes unit tests (shared PDF fetch utility)
# ---------------------------------------------------------------------------


def test_fetch_pdf_bytes_returns_content_on_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"%PDF-fake")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = _fetch_pdf_bytes(client, "http://example.com/sheet.pdf")

    assert result == b"%PDF-fake"


def test_fetch_pdf_bytes_uses_provided_url() -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, content=b"%PDF")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        _fetch_pdf_bytes(client, "http://example.com/my.pdf")

    assert "my.pdf" in seen_urls[0]


def test_fetch_pdf_bytes_retries_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_ingest.scraper.sources._pdf.time.sleep", lambda _: None)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(500, text="error")
        return httpx.Response(200, content=b"%PDF")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = _fetch_pdf_bytes(client, "http://example.com/sheet.pdf")

    assert len(attempts) == 3
    assert result == b"%PDF"


def test_fetch_pdf_bytes_raises_after_retries_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_ingest.scraper.sources._pdf.time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="always fails")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            _fetch_pdf_bytes(client, "http://example.com/sheet.pdf")


# ---------------------------------------------------------------------------
# PdfSourceAdapter base class
# ---------------------------------------------------------------------------


def test_classicalcomposersposter_is_pdf_source_adapter() -> None:
    assert issubclass(ClassicalComposersPosterAdapter, PdfSourceAdapter)


# ---------------------------------------------------------------------------
# _parse_rows unit tests (Docling mocked)
# ---------------------------------------------------------------------------


def _make_converter_mock(tables: list[pd.DataFrame] | None = None, markdown: str = "") -> MagicMock:
    """Build a mock DocumentConverter whose convert() returns a fake result."""
    mock_table_objs = []
    for df in tables or []:
        t = MagicMock()
        t.export_to_dataframe.return_value = df
        mock_table_objs.append(t)

    doc = MagicMock()
    doc.tables = mock_table_objs
    doc.export_to_markdown.return_value = markdown

    result = MagicMock()
    result.document = doc

    converter = MagicMock()
    converter.convert.return_value = result
    return converter


def test_parse_rows_extracts_from_table_with_header() -> None:
    df = pd.DataFrame(
        [
            ["Name", "Born", "Died"],
            ["Bach, Johann Sebastian", "1685", "1750"],
            ["Mozart, Wolfgang Amadeus", "1756", "1791"],
        ]
    )
    mock_converter = _make_converter_mock(tables=[df])

    with patch(_DC_PATH, return_value=mock_converter):
        rows = _parse_rows(b"fake")

    assert len(rows) == 2
    assert rows[0]["name"] == "Bach, Johann Sebastian"
    assert rows[0]["born"] == "1685"
    assert rows[0]["died"] == "1750"
    assert rows[1]["name"] == "Mozart, Wolfgang Amadeus"


def test_parse_rows_falls_back_to_markdown_when_no_tables() -> None:
    markdown = "Bach, Johann Sebastian 1685 1750\nMozart, Wolfgang Amadeus 1756 1791"
    mock_converter = _make_converter_mock(tables=[], markdown=markdown)

    with patch(_DC_PATH, return_value=mock_converter):
        rows = _parse_rows(b"fake")

    assert len(rows) == 2
    assert rows[0]["name"] == "Bach, Johann Sebastian"
    assert rows[0]["born"] == "1685"


def test_parse_rows_passes_max_pages_to_converter() -> None:
    mock_converter = _make_converter_mock(tables=[], markdown="")

    with patch(_DC_PATH, return_value=mock_converter):
        _parse_rows(b"fake", max_pages=3)

    mock_converter.convert.assert_called_once()
    _, kwargs = mock_converter.convert.call_args
    assert kwargs.get("max_num_pages") == 3


def test_parse_rows_omits_max_pages_when_none() -> None:
    mock_converter = _make_converter_mock(tables=[], markdown="")

    with patch(_DC_PATH, return_value=mock_converter):
        _parse_rows(b"fake", max_pages=None)

    _, kwargs = mock_converter.convert.call_args
    assert "max_num_pages" not in kwargs


def test_parse_rows_skips_empty_tables() -> None:
    empty_df = pd.DataFrame()
    markdown = "Chopin, Frédéric 1810 1849"
    mock_converter = _make_converter_mock(tables=[empty_df], markdown=markdown)

    with patch(_DC_PATH, return_value=mock_converter):
        rows = _parse_rows(b"fake")

    assert len(rows) == 1
    assert rows[0]["name"] == "Chopin, Frédéric"


def test_parse_rows_omits_born_and_died_when_absent() -> None:
    df = pd.DataFrame(
        [
            ["Name", "Born", "Died"],
            ["Anonymous", "", ""],
        ]
    )
    mock_converter = _make_converter_mock(tables=[df])

    with patch(_DC_PATH, return_value=mock_converter):
        rows = _parse_rows(b"fake")

    assert len(rows) == 1
    assert rows[0]["born"] is None
    assert rows[0]["died"] is None


# ---------------------------------------------------------------------------
# ClassicalComposersPosterAdapter.fetch integration tests
# ---------------------------------------------------------------------------


def test_fetch_yields_entity_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "composer_ingest.scraper.sources.classicalcomposersposter.ClassicalComposersPosterAdapter._download_pdf",
        lambda self: b"fake",
    )
    monkeypatch.setattr(
        "composer_ingest.scraper.sources.classicalcomposersposter._parse_rows",
        lambda data, max_pages=None: [
            {"name": "Bach, Johann Sebastian", "born": "1685", "died": "1750"},
        ],
    )

    records = list(ClassicalComposersPosterAdapter().fetch())
    assert len(records) == 1
    rec = records[0]
    assert rec.name == "Bach, Johann Sebastian"
    assert rec.source_name == "classicalcomposersposter"
    assert rec.id == "classicalcomposersposter:bach-johann-sebastian"
    assert rec.kind == "person"


def test_fetch_attaches_born_and_died_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "composer_ingest.scraper.sources.classicalcomposersposter.ClassicalComposersPosterAdapter._download_pdf",
        lambda self: b"fake",
    )
    monkeypatch.setattr(
        "composer_ingest.scraper.sources.classicalcomposersposter._parse_rows",
        lambda data, max_pages=None: [
            {"name": "Beethoven, Ludwig van", "born": "1770", "died": "1827"},
        ],
    )

    (rec,) = list(ClassicalComposersPosterAdapter().fetch())
    predicates = {c.predicate: c.value for c in rec.claims}
    assert predicates["born_on"] == "1770"
    assert predicates["died_on"] == "1827"


def test_fetch_omits_claims_when_dates_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "composer_ingest.scraper.sources.classicalcomposersposter.ClassicalComposersPosterAdapter._download_pdf",
        lambda self: b"fake",
    )
    monkeypatch.setattr(
        "composer_ingest.scraper.sources.classicalcomposersposter._parse_rows",
        lambda data, max_pages=None: [
            {"name": "Unknown Composer", "born": None, "died": None},
        ],
    )

    (rec,) = list(ClassicalComposersPosterAdapter().fetch())
    assert rec.claims == ()


def test_fetch_passes_max_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "composer_ingest.scraper.sources.classicalcomposersposter.ClassicalComposersPosterAdapter._download_pdf",
        lambda self: b"fake",
    )
    captured: list[int | None] = []

    def fake_parse(data: bytes, max_pages: int | None = None) -> list[Any]:
        captured.append(max_pages)
        return []

    monkeypatch.setattr(
        "composer_ingest.scraper.sources.classicalcomposersposter._parse_rows",
        fake_parse,
    )

    list(ClassicalComposersPosterAdapter().fetch(max_pages=3))
    assert captured == [3]


def test_fetch_skips_empty_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "composer_ingest.scraper.sources.classicalcomposersposter.ClassicalComposersPosterAdapter._download_pdf",
        lambda self: b"fake",
    )
    monkeypatch.setattr(
        "composer_ingest.scraper.sources.classicalcomposersposter._parse_rows",
        lambda data, max_pages=None: [
            {"name": "Valid Composer", "born": "1800", "died": "1870"},
            {"name": "", "born": None, "died": None},
        ],
    )

    records = list(ClassicalComposersPosterAdapter().fetch())
    assert len(records) == 1
    assert records[0].name == "Valid Composer"


def test_adapter_registered_in_registry() -> None:
    from composer_ingest.scraper.sources import REGISTRY

    assert "classicalcomposersposter" in REGISTRY
    assert isinstance(REGISTRY["classicalcomposersposter"], ClassicalComposersPosterAdapter)


# ---------------------------------------------------------------------------
# NaN / missing cell handling
# ---------------------------------------------------------------------------


def test_parse_rows_skips_nan_name_cells() -> None:
    """Pandas NaN in the name column must not produce a composer named 'nan'."""
    import numpy as np

    df = pd.DataFrame(
        [
            ["Name", "Born", "Died"],
            ["Bach, Johann Sebastian", "1685", "1750"],
            [np.nan, "1800", "1870"],
        ]
    )
    mock_converter = _make_converter_mock(tables=[df])

    with patch(_DC_PATH, return_value=mock_converter):
        rows = _parse_rows(b"fake")

    names = [r["name"] for r in rows]
    assert "nan" not in names
    assert len(rows) == 1
    assert rows[0]["name"] == "Bach, Johann Sebastian"


def test_parse_rows_nan_date_cells_treated_as_absent() -> None:
    """Pandas NaN in born/died columns must produce None, not 'nan'."""
    import numpy as np

    df = pd.DataFrame(
        [
            ["Name", "Born", "Died"],
            ["Chopin, Frédéric", "1810", np.nan],
        ]
    )
    mock_converter = _make_converter_mock(tables=[df])

    with patch(_DC_PATH, return_value=mock_converter):
        rows = _parse_rows(b"fake")

    assert rows[0]["died"] is None
    assert rows[0]["born"] == "1810"


# ---------------------------------------------------------------------------
# Headerless table date-column inference
# ---------------------------------------------------------------------------


def test_parse_rows_infers_date_columns_without_header() -> None:
    """Tables with no header row should still extract born/died via column inference."""
    df = pd.DataFrame(
        [
            ["Bach, Johann Sebastian", "1685", "1750"],
            ["Mozart, Wolfgang Amadeus", "1756", "1791"],
            ["Beethoven, Ludwig van", "1770", "1827"],
        ]
    )
    mock_converter = _make_converter_mock(tables=[df])

    with patch(_DC_PATH, return_value=mock_converter):
        rows = _parse_rows(b"fake")

    assert len(rows) == 3
    assert rows[0]["born"] == "1685"
    assert rows[0]["died"] == "1750"


def test_infer_date_columns_returns_none_when_no_year_density() -> None:
    df = pd.DataFrame(
        [
            ["Bach, Johann Sebastian", "German", "Baroque"],
            ["Mozart, Wolfgang Amadeus", "Austrian", "Classical"],
        ]
    )
    born_col, died_col = _infer_date_columns(df)
    assert born_col is None
    assert died_col is None


def test_infer_date_columns_finds_two_date_columns() -> None:
    df = pd.DataFrame(
        [
            ["Bach, J.S.", "1685", "1750"],
            ["Mozart, W.A.", "1756", "1791"],
        ]
    )
    born_col, died_col = _infer_date_columns(df)
    assert born_col == 1
    assert died_col == 2


# ---------------------------------------------------------------------------
# Markdown fallback warning
# ---------------------------------------------------------------------------


def test_parse_rows_logs_warning_on_markdown_fallback(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    mock_converter = _make_converter_mock(tables=[], markdown="Bach 1685 1750")

    with patch(_DC_PATH, return_value=mock_converter):
        with caplog.at_level(
            logging.WARNING, logger="composer_ingest.scraper.sources.classicalcomposersposter.parse"
        ):
            rows = _parse_rows(b"fake")

    assert any("falling back" in r.message.lower() for r in caplog.records)
    assert len(rows) == 1
