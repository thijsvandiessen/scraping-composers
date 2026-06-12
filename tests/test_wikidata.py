"""Tests for folding Wikidata SPARQL result rows into SourceRecords."""

import urllib.parse
from typing import Any

import httpx
import pytest

from composer_ingest.sources import SourceClaim
from composer_ingest.sources.wikidata import _fetch_metrics, _fetch_page, _records_from_rows


def row(qid: str, label: str | None = None, **vars: str) -> dict[str, Any]:
    """A SPARQL JSON result row; ``vars`` are bound result variables."""
    bindings: dict[str, Any] = {"item": {"type": "uri", "value": f"http://www.wikidata.org/entity/{qid}"}}
    if label is not None:
        bindings["itemLabel"] = {"type": "literal", "value": label}
    for var, value in vars.items():
        bindings[var] = {"type": "literal", "value": value}
    return bindings


def test_rows_for_one_composer_fold_into_one_record() -> None:
    rows = [
        row(
            "Q255",
            "Ludwig van Beethoven",
            birth="1770-12-16T00:00:00Z",
            death="1827-03-26T00:00:00Z",
            birthPlaceLabel="Bonn",
            genreLabel="opera",
        ),
        row("Q255", "Ludwig van Beethoven", birth="1770-12-16T00:00:00Z", genreLabel="symphony"),
    ]
    (record,) = _records_from_rows(rows)

    assert record.external_id == "Q255"
    assert record.name == "Ludwig van Beethoven"
    assert record.url == "https://www.wikidata.org/wiki/Q255"
    assert record.claims == (
        SourceClaim("has_profession", "profession", "composer"),
        SourceClaim("born_on", value="1770-12-16"),  # time part stripped
        SourceClaim("died_on", value="1827-03-26"),
        SourceClaim("born_in", "place", "Bonn"),
        SourceClaim("has_genre", "genre", "opera"),  # duplicates folded
        SourceClaim("has_genre", "genre", "symphony"),
    )
    assert record.raw["genreLabel"] == ["opera", "symphony"]


def test_minimal_composer_still_claims_the_profession() -> None:
    (record,) = _records_from_rows([row("Q1", "Anonymous Composer")])
    assert record.claims == (SourceClaim("has_profession", "profession", "composer"),)


def test_items_without_english_label_are_skipped() -> None:
    # the label service echoes the QID back when no label exists
    rows = [row("Q2", "Q2", birth="1900-01-01T00:00:00Z"), row("Q3", "Maurice Ravel"), row("Q4")]
    records = _records_from_rows(rows)
    assert [r.name for r in records] == ["Maurice Ravel"]


def test_claim_objects_without_english_label_are_skipped() -> None:
    (record,) = _records_from_rows([row("Q5", "Some Composer", birthPlaceLabel="Q1234")])
    assert record.claims == (SourceClaim("has_profession", "profession", "composer"),)


def test_unknown_value_blank_nodes_are_ignored() -> None:
    rows = [
        {
            "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q6"},
            "itemLabel": {"type": "literal", "value": "Unknown Birthday"},
            "birth": {"type": "bnode", "value": "t42"},  # Wikidata "unknown value"
        }
    ]
    (record,) = _records_from_rows(rows)
    assert record.claims == (SourceClaim("has_profession", "profession", "composer"),)


def test_negative_years_survive_date_truncation() -> None:
    (record,) = _records_from_rows([row("Q7", "Ancient Composer", birth="-0500-01-01T00:00:00Z")])
    assert SourceClaim("born_on", value="-0500-01-01") in record.claims


def test_truncated_body_is_retried_via_uncached_post(monkeypatch: pytest.MonkeyPatch) -> None:
    """A WDQS timeout can truncate the body mid-stream yet still return 200;
    the retry must not hit the edge cache (POST bypasses it) or it would get
    the same broken body back for 300s."""
    monkeypatch.setattr("composer_ingest.sources.wikidata.time.sleep", lambda _: None)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            # truncated mid-stream by a WDQS timeout, still served as 200 OK
            return httpx.Response(200, text='{"results": {"bindings": [{"item": {"va')
        return httpx.Response(200, json={"results": {"bindings": []}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert _fetch_page(client, offset=0) == []

    assert len(requests) == 2
    assert all(r.method == "POST" for r in requests)  # POSTs are never edge-cached


def metrics_row(qid: str, **vars: str) -> dict[str, Any]:
    bindings: dict[str, Any] = {"item": {"type": "uri", "value": f"http://www.wikidata.org/entity/{qid}"}}
    for var, value in vars.items():
        bindings[var] = {"type": "literal", "value": value}
    return bindings


def test_fetch_metrics_keys_rows_by_qid() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        form = urllib.parse.parse_qs(request.read().decode())
        assert "wd:Q255 wd:Q7" in form["query"][0]  # VALUES block
        rows = [
            metrics_row("Q255", sitelinks="273", statements="547", identifiers="370", works="342"),
            metrics_row("Q7", sitelinks="1", statements="8", identifiers="2", works="0"),
        ]
        return httpx.Response(200, json={"results": {"bindings": rows}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        metrics = _fetch_metrics(client, ["Q255", "Q7"])

    assert metrics["Q255"] == {"sitelinks": "273", "statements": "547", "identifiers": "370", "works": "342"}
    assert metrics["Q7"]["works"] == "0"


def test_movement_becomes_an_in_movement_claim() -> None:
    (record,) = _records_from_rows([row("Q8", "Baroque Person", movementLabel="Baroque music")])
    assert SourceClaim("in_movement", "movement", "Baroque music") in record.claims


def test_metrics_become_literal_claims_and_land_in_raw() -> None:
    metrics = {"Q255": {"sitelinks": "273", "statements": "547", "identifiers": "370", "works": "342"}}
    (record,) = _records_from_rows([row("Q255", "Ludwig van Beethoven")], metrics)

    assert SourceClaim("sitelink_count", value="273") in record.claims
    assert SourceClaim("statement_count", value="547") in record.claims
    assert SourceClaim("identifier_count", value="370") in record.claims
    assert SourceClaim("work_count", value="342") in record.claims
    assert record.raw["sitelinks"] == "273"


def test_records_without_metrics_get_no_metric_claims() -> None:
    (record,) = _records_from_rows([row("Q9", "Uncounted Composer")], {"Q999": {"sitelinks": "5"}})
    assert record.claims == (SourceClaim("has_profession", "profession", "composer"),)
