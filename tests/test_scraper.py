"""Tests for the base Scraper: it threads max_pages and stamps every document."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from composer_ingest.document import Document, content_hash, entity_document
from composer_ingest.scraper import Scraper, SourceConfig


def test_scraper_threads_max_pages_and_stamps_documents() -> None:
    seen_max: list[int | None] = []

    def pages(client: httpx.Client, max_pages: int | None) -> Iterator[dict[str, Any]]:
        seen_max.append(max_pages)
        yield {"name": "Mozart"}
        yield {"name": "Haydn"}

    def parse(raw: dict[str, Any]) -> Iterator[Document]:
        yield entity_document(id=raw["name"], name=raw["name"])

    scraper: Scraper[dict[str, Any]] = Scraper(
        SourceConfig(name="fake", base_url="https://fake.example"), pages, parse
    )

    docs = list(scraper.fetch_documents(max_pages=3))

    assert seen_max == [3]
    assert [d.id for d in docs] == ["Mozart", "Haydn"]
    for doc in docs:
        assert doc.source_name == "fake"
        assert doc.ingested_at  # stamped with a timestamp
        assert doc.content_hash == content_hash(doc.body)
    assert scraper.NAME == "fake"
    assert scraper.BASE_URL == "https://fake.example"


def test_scraper_sets_user_agent_and_extra_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200, json={})

    # route the scraper's client through a mock transport (no real network)
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def fake_client(**kwargs: Any) -> httpx.Client:
        kwargs["transport"] = transport
        return real_client(**kwargs)

    monkeypatch.setattr("composer_ingest.scraper.httpx.Client", fake_client)

    def pages(client: httpx.Client, max_pages: int | None) -> Iterator[dict[str, Any]]:
        client.get("https://fake.example/ping")  # exercises the configured client
        yield {}

    def parse(raw: dict[str, Any]) -> Iterator[Document]:
        yield from ()

    scraper: Scraper[dict[str, Any]] = Scraper(
        SourceConfig(
            name="fake",
            base_url="https://fake.example",
            user_agent="ua/1.0",
            headers={"Accept-Language": "en"},
        ),
        pages,
        parse,
    )
    list(scraper.fetch_documents())

    assert captured["user-agent"] == "ua/1.0"
    assert captured["accept-language"] == "en"
