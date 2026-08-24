"""Tests for folding Wikidata SPARQL result rows into SourceRecords."""

import re
import urllib.parse
from typing import Any

import httpx
import pytest
from composer_schema import SourceClaim
from composer_scrapers.wikidata import WikidataAdapter
from composer_scrapers.wikidata.parse import _format_time, _records_from_rows
from composer_scrapers.wikidata.query import (
    LABEL_LANGUAGES,
    MULTI_QUERY,
    QUERY,
    _fetch_metrics,
    _fetch_page,
    _fetch_qids,
    _run_query,
)


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


def test_items_the_label_service_cannot_name_are_skipped() -> None:
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


def test_year_precision_dates_are_truncated_to_the_year() -> None:
    # precision 9 = year: the padded 01-01 is not real, so it must be dropped
    (record,) = _records_from_rows(
        [
            row(
                "Q10",
                "Year Only",
                birth="1756-01-01T00:00:00Z",
                birthPrecision="9",
                death="1791-12-05T00:00:00Z",
                deathPrecision="11",
            )
        ]
    )
    assert SourceClaim("born_on", value="1756") in record.claims
    assert SourceClaim("died_on", value="1791-12-05") in record.claims  # day precision kept in full


def test_month_precision_dates_keep_year_and_month() -> None:
    # precision 10 = month: keep YYYY-MM, drop the padded day
    (record,) = _records_from_rows(
        [row("Q11", "Month Only", birth="1810-03-01T00:00:00Z", birthPrecision="10")]
    )
    assert SourceClaim("born_on", value="1810-03") in record.claims


def test_year_precision_negative_year_keeps_its_sign() -> None:
    (record,) = _records_from_rows([row("Q12", "Ancient", birth="-0550-01-01T00:00:00Z", birthPrecision="9")])
    assert SourceClaim("born_on", value="-0550") in record.claims


@pytest.mark.parametrize(
    "value,precision,expected",
    [
        ("1756-01-27T00:00:00Z", "11", "1756-01-27"),  # day
        ("1810-03-01T00:00:00Z", "10", "1810-03"),  # month
        ("1756-01-01T00:00:00Z", "9", "1756"),  # year
        ("1700-01-01T00:00:00Z", "8", "1700"),  # decade -> coarse, rendered as the year
        ("1700-01-01T00:00:00Z", "7", "1700"),  # century -> coarse, rendered as the year
        ("-0550-01-01T00:00:00Z", "9", "-0550"),  # BCE keeps its sign
        ("1756-01-27T00:00:00Z", None, "1756-01-27"),  # missing precision -> assume full
        ("1756-01-27T00:00:00Z", "garbage", "1756-01-27"),  # unparseable precision -> assume full
        # not a time literal (e.g. an "unknown value" node) -> passed through, never raises
        (
            "http://www.wikidata.org/.well-known/genid/abc",
            "11",
            "http://www.wikidata.org/.well-known/genid/abc",
        ),
    ],
)
def test_format_time(value: str, precision: str | None, expected: str) -> None:
    assert _format_time(value, precision) == expected


def test_truncated_body_is_retried_via_uncached_post(monkeypatch: pytest.MonkeyPatch) -> None:
    """A WDQS timeout can truncate the body mid-stream yet still return 200;
    the retry must not hit the edge cache (POST bypasses it) or it would get
    the same broken body back for 300s."""
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            # truncated mid-stream by a WDQS timeout, still served as 200 OK
            return httpx.Response(200, text='{"results": {"bindings": [{"item": {"va')
        return httpx.Response(200, json={"results": {"bindings": []}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert _run_query(client, "SELECT ?item WHERE {}", "test") == []

    assert len(requests) == 2
    assert all(r.method == "POST" for r in requests)  # POSTs are never edge-cached


def test_fetch_page_binds_the_batch_as_values() -> None:
    """Pages are driven from a client-side id list, so the batch goes out as a
    VALUES block -- no server-side cursor, and nothing to seek past."""
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(urllib.parse.parse_qs(request.read().decode())["query"][0])
        return httpx.Response(200, json={"results": {"bindings": [row("Q6600"), row("Q7")]}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        _fetch_page(client, ["Q6600", "Q7"])

    assert "VALUES ?item { wd:Q6600 wd:Q7 }" in captured[0]
    assert "OFFSET" not in captured[0]
    assert "FILTER" not in captured[0]


def test_labels_fall_back_to_the_language_agnostic_ones() -> None:
    """Wikidata is migrating names that are spelled the same everywhere out of
    "en" and into "mul". Q254 has only a mul label, so asking for "en" alone
    gets "Q254" echoed back and drops Mozart from the dataset entirely."""
    assert LABEL_LANGUAGES == "en,mul"
    for template in (QUERY, MULTI_QUERY):
        rendered = template.format(values="wd:Q254", languages=LABEL_LANGUAGES)
        assert 'bd:serviceParam wikibase:language "en,mul"' in rendered


def test_multi_valued_fields_are_unioned_not_optional() -> None:
    """OPTIONALs multiply: aliases (every language) x genres x countries ran to
    tens of thousands of rows for a well-documented composer and truncated the
    response. UNION makes a page cost the sum, not the product."""
    assert "OPTIONAL" not in MULTI_QUERY
    assert MULTI_QUERY.count("UNION") == 3
    for var in ("?countryLabel", "?genreLabel", "?movementLabel", "?alias"):
        assert var in MULTI_QUERY
        assert var not in QUERY  # split out of the single-valued query


def test_fetch_page_concatenates_both_queries_label_bearing_rows_first() -> None:
    """``_fold_rows`` takes an item's label from the first row it sees, so the
    labelled QUERY rows have to lead the MULTI_QUERY rows."""
    responses = {
        "?itemLabel": [row("Q7294", "Johannes Brahms", birth="1833-05-07T00:00:00Z")],
        "UNION": [row("Q7294", genreLabel="symphony"), row("Q7294", alias="Brahms")],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        query = urllib.parse.parse_qs(request.read().decode())["query"][0]
        key = "?itemLabel" if "?itemLabel" in query else "UNION"
        return httpx.Response(200, json={"results": {"bindings": responses[key]}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        (record,) = _records_from_rows(_fetch_page(client, ["Q7294"]))

    assert record.name == "Johannes Brahms"
    assert record.claims == (
        SourceClaim("has_profession", "profession", "composer"),
        SourceClaim("born_on", value="1833-05-07"),
        SourceClaim("has_genre", "genre", "symphony"),
        SourceClaim("also_known_as", value="Brahms"),
    )


def test_fetch_page_fails_when_wdqs_drops_an_item() -> None:
    """Every pattern but VALUES is OPTIONAL, so a bound item must come back.
    A short page is a silent hole (issue #181) and has to fail the run."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": {"bindings": [row("Q6600")]}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="1 of 2 requested items"):
            _fetch_page(client, ["Q6600", "Q7"])


def test_fetch_qids_orders_the_population_numerically() -> None:
    """Lexicographic order would bury the low (famous) QIDs behind every
    Q1000000+ id, and it is what broke keyset paging in the first place."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "ORDER BY" not in urllib.parse.parse_qs(request.read().decode())["query"][0]
        rows = [row(q) for q in ("Q1339", "Q101424951", "Q255", "Q7294", "Q1339")]
        return httpx.Response(200, json={"results": {"bindings": rows}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert _fetch_qids(client) == ["Q255", "Q1339", "Q7294", "Q101424951"]


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


def test_query_requests_the_musicbrainz_artist_id() -> None:
    assert "wdt:P434 ?musicbrainz" in QUERY
    assert "?musicbrainz\n" in QUERY  # projected in the SELECT clause


def test_musicbrainz_id_becomes_a_literal_claim() -> None:
    (record,) = _records_from_rows(
        [row("Q255", "Ludwig van Beethoven", musicbrainz="1f9df192-a621-4f54-8850-2c5373b7eac9")]
    )
    assert SourceClaim("musicbrainz_id", value="1f9df192-a621-4f54-8850-2c5373b7eac9") in record.claims
    assert record.raw["musicbrainz"] == ["1f9df192-a621-4f54-8850-2c5373b7eac9"]


def test_aliases_are_stored_verbatim_not_date_truncated() -> None:
    # aliases must not run through the time formatter, which splits on "T"
    (record,) = _records_from_rows([row("Q13", "Pyotr Ilyich Tchaikovsky", alias="Peter Tschaikowsky")])
    assert SourceClaim("also_known_as", value="Peter Tschaikowsky") in record.claims


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


# ---------------------------------------------------------------------------
# _run_query
# ---------------------------------------------------------------------------


def test_run_query_raises_after_all_retries_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            _run_query(client, "SELECT ?x WHERE {}", "test query")


def test_run_query_honors_retry_after_header(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("composer_http.time.sleep", sleeps.append)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "30"}, text="Rate limited")
        return httpx.Response(200, json={"results": {"bindings": []}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = _run_query(client, "SELECT ?x WHERE {}", "test query")

    assert result == []
    assert 30 in sleeps  # Retry-After overrides the 2^1=2 exponential backoff


def test_run_query_retries_on_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(200, text='{"results": {"bindings": [{"item": {"va')
        return httpx.Response(200, json={"results": {"bindings": []}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = _run_query(client, "SELECT ?x WHERE {}", "test query")

    assert len(attempts) == 3
    assert result == []


def _fake_wdqs(monkeypatch: pytest.MonkeyPatch, population: list[str]) -> list[str]:
    """Stand WDQS up over ``population``: the id query returns all of them, the
    detail and metrics queries answer whatever VALUES block they are given.
    Returns the list the queries got asked for, in request order."""
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = urllib.parse.parse_qs(request.read().decode())["query"][0]
        if "VALUES" not in query:  # the id query
            return httpx.Response(200, json={"results": {"bindings": [row(q) for q in population]}})
        qids = re.findall(r"wd:(Q\d+)", query.split("VALUES", 1)[1].split("}", 1)[0])
        if "?sitelinks" in query:
            return httpx.Response(200, json={"results": {"bindings": []}})
        asked.extend(qids)
        rows = [row(qid, f"Composer {qid}") for qid in qids]
        return httpx.Response(200, json={"results": {"bindings": rows}})

    monkeypatch.setattr(
        "composer_scrapers.wikidata.new_client",
        lambda **_: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr("composer_scrapers.wikidata.time.sleep", lambda _: None)
    return asked


def test_fetch_covers_every_composer_across_page_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression from issue #181: a cursor that disagreed with the server's
    ordering skipped whole ranges. Batching a client-side id list cannot."""
    population = ["Q254", "Q255", "Q1339", "Q7294", "Q101424951"] + [f"Q{n}" for n in range(2000, 3200)]
    _fake_wdqs(monkeypatch, population)
    monkeypatch.setattr("composer_scrapers.wikidata.PAGE_SIZE", 500)

    fetched = [doc.id for doc in WikidataAdapter().fetch()]

    assert len(fetched) == len(population)
    assert set(fetched) == set(population)


def test_fetch_orders_pages_so_a_capped_run_gets_the_famous_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    population = [f"Q{n}" for n in range(1_000_000, 1_000_010)] + ["Q254", "Q255", "Q1339"]
    _fake_wdqs(monkeypatch, population)
    monkeypatch.setattr("composer_scrapers.wikidata.PAGE_SIZE", 3)

    fetched = [doc.id for doc in WikidataAdapter().fetch(max_pages=1)]

    assert fetched == ["Q254", "Q255", "Q1339"]


def test_fetch_refuses_an_empty_population(monkeypatch: pytest.MonkeyPatch) -> None:
    """An id query that comes back empty means WDQS is broken, not that
    Wikidata has no composers -- yielding nothing would wipe silver clean."""
    _fake_wdqs(monkeypatch, [])

    with pytest.raises(RuntimeError, match="no composer ids"):
        list(WikidataAdapter().fetch())
