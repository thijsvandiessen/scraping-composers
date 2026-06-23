"""Tests for the Classical Composers Poster PDF source."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from composer_ingest.scraper.sources.classicalcomposersposter import ClassicalComposersPosterAdapter
from composer_ingest.scraper.sources.classicalcomposersposter.fetch import _fetch_pdf
from composer_ingest.scraper.sources.classicalcomposersposter.parse import _parse_rows

# ---------------------------------------------------------------------------
# Minimal real PDF bytes for _parse_rows tests (generated with reportlab-free
# approach: a hand-crafted minimal valid PDF containing a simple text table).
# We keep it small; pdfplumber's text extraction is exercised, not full layout.
# ---------------------------------------------------------------------------

_MINIMAL_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 120>>
stream
BT /F1 12 Tf 50 750 Td (Name                Born  Died) Tj
0 -20 Td (Bach, Johann Sebastian  1685  1750) Tj
0 -20 Td (Mozart, Wolfgang Amadeus  1756  1791) Tj ET
endstream
endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000266 00000 n
0000000438 00000 n
trailer<</Size 6/Root 1 0 R>>
startxref
521
%%EOF"""


# ---------------------------------------------------------------------------
# _fetch_pdf unit tests
# ---------------------------------------------------------------------------


def test_fetch_pdf_returns_bytes_on_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"%PDF-fake")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = _fetch_pdf(client)

    assert result == b"%PDF-fake"


def test_fetch_pdf_sends_browser_headers() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        return httpx.Response(200, content=b"%PDF")

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": "test-agent", "Referer": "http://example.com"},
    ) as client:
        _fetch_pdf(client)

    assert seen[0]["user-agent"] == "test-agent"


def test_fetch_pdf_retries_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "composer_ingest.scraper.sources.classicalcomposersposter.fetch.time.sleep",
        lambda _: None,
    )
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(500, text="error")
        return httpx.Response(200, content=b"%PDF")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = _fetch_pdf(client)

    assert len(attempts) == 3
    assert result == b"%PDF"


def test_fetch_pdf_raises_after_retries_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "composer_ingest.scraper.sources.classicalcomposersposter.fetch.time.sleep",
        lambda _: None,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="always fails")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            _fetch_pdf(client)


# ---------------------------------------------------------------------------
# _parse_rows unit tests
# ---------------------------------------------------------------------------


_CCP_PARSE_PATH = "composer_ingest.scraper.sources.classicalcomposersposter.parse.pdfplumber.open"


def test_parse_rows_returns_rows_from_text(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_open(source: Any) -> Any:
        class FakePage:
            def extract_table(self) -> None:
                return None

            def extract_text(self) -> str:
                return "Bach, Johann Sebastian 1685 1750\nMozart, Wolfgang Amadeus 1756 1791"

        class FakePDF:
            pages = [FakePage()]

            def __enter__(self) -> FakePDF:
                return self

            def __exit__(self, *args: Any) -> None:
                pass

        return FakePDF()

    monkeypatch.setattr(_CCP_PARSE_PATH, fake_open)
    rows = _parse_rows(b"fake")
    assert len(rows) == 2
    assert rows[0]["name"] == "Bach, Johann Sebastian"
    assert rows[0]["born"] == "1685"
    assert rows[0]["died"] == "1750"


def test_parse_rows_respects_max_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    pages_visited: list[int] = []

    def fake_open(source: Any) -> Any:
        class FakePage:
            def __init__(self, n: int) -> None:
                self.n = n

            def extract_table(self) -> None:
                return None

            def extract_text(self) -> str:
                pages_visited.append(self.n)
                return f"Composer {self.n} 1800 1870"

        class FakePDF:
            pages = [FakePage(i) for i in range(5)]

            def __enter__(self) -> FakePDF:
                return self

            def __exit__(self, *args: Any) -> None:
                pass

        return FakePDF()

    monkeypatch.setattr(_CCP_PARSE_PATH, fake_open)
    _parse_rows(b"fake", max_pages=2)
    assert len(pages_visited) == 2


def test_parse_rows_uses_table_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_open(source: Any) -> Any:
        class FakePage:
            def extract_table(self) -> list[list[str]]:
                return [
                    ["Name", "Born", "Died"],
                    ["Beethoven, Ludwig van", "1770", "1827"],
                    ["Chopin, Frédéric", "1810", "1849"],
                ]

            def extract_text(self) -> str:
                return ""

        class FakePDF:
            pages = [FakePage()]

            def __enter__(self) -> FakePDF:
                return self

            def __exit__(self, *args: Any) -> None:
                pass

        return FakePDF()

    monkeypatch.setattr(_CCP_PARSE_PATH, fake_open)
    rows = _parse_rows(b"fake")
    assert len(rows) == 2
    assert rows[0]["name"] == "Beethoven, Ludwig van"
    assert rows[0]["born"] == "1770"
    assert rows[0]["died"] == "1827"


# ---------------------------------------------------------------------------
# ClassicalComposersPosterAdapter.fetch integration tests
# ---------------------------------------------------------------------------


def test_fetch_yields_entity_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "composer_ingest.scraper.sources.classicalcomposersposter._fetch_pdf",
        lambda client: b"fake",
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
        "composer_ingest.scraper.sources.classicalcomposersposter._fetch_pdf",
        lambda client: b"fake",
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
        "composer_ingest.scraper.sources.classicalcomposersposter._fetch_pdf",
        lambda client: b"fake",
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
        "composer_ingest.scraper.sources.classicalcomposersposter._fetch_pdf",
        lambda client: b"fake",
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
        "composer_ingest.scraper.sources.classicalcomposersposter._fetch_pdf",
        lambda client: b"fake",
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
