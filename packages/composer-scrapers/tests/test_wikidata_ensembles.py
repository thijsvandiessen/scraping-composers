"""Tests for the Wikidata ensembles adapter: orchestras and choirs, the anchors of #350."""

import urllib.parse
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from composer_http import PageCache
from composer_http.testing import mock_session
from composer_schema import CLASSICAL_NAME_CUES
from composer_scrapers import REGISTRY, SourceClaim
from composer_scrapers.wikidata import WikidataEnsemblesAdapter, ensembles
from composer_scrapers.wikidata.ensembles import (
    CLASSICAL_GENRES,
    ENSEMBLE_ID_QUERY,
    ENSEMBLE_MULTI_QUERY,
    ENSEMBLE_QUERY,
    GENRE_GROUP_ID_QUERY,
    MUSICAL_GROUPS,
    NAME_CUES,
    NAME_GROUP_ID_QUERY,
    _documents_from_rows,
    _fetch_ensemble_page,
    _fetch_ensemble_qids,
    ensemble_type,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)
ORCHESTRA, CHOIR = "Q42998", "Q131186"


def uri(qid: str) -> dict[str, str]:
    return {"type": "uri", "value": f"http://www.wikidata.org/entity/{qid}"}


def lit(value: str) -> dict[str, str]:
    return {"type": "literal", "value": value}


def row(qid: str, label: str | None = None, **vars: str) -> dict[str, Any]:
    """A result row; a value written "Q1=Label" binds an item and its label,
    any other value a literal."""
    bindings: dict[str, Any] = {"item": uri(qid)}
    if label is not None:
        bindings["itemLabel"] = lit(label)
    for var, value in vars.items():
        if "=" in value:
            other, other_label = value.split("=", 1)
            bindings[var] = uri(other)
            bindings[f"{var}Label"] = lit(other_label)
        else:
            bindings[var] = lit(value)
    return bindings


def root(qid: str, cls: str) -> dict[str, Any]:
    return {"item": uri(qid), "root": uri(cls)}


def claims_of(rows: list[dict[str, Any]], population: set[str] | None = None) -> tuple[SourceClaim, ...]:
    (document,) = _documents_from_rows(rows, population or set(), NOW)
    return document.claims


def test_an_orchestra_becomes_a_qid_pinned_ensemble_document() -> None:
    rows = [
        row("Q1", "Vienna Philharmonic", hq="Q1741=Vienna", hqSettlement="true", musicbrainz="mb-1"),
        root("Q1", ORCHESTRA),
        {"item": uri("Q1"), "name": lit("Wiener Philharmoniker")},
    ]
    (document,) = _documents_from_rows(rows, {"Q1"}, NOW)

    assert document.id == "Q1"
    assert document.url == "https://www.wikidata.org/wiki/Q1"
    assert document.kind == "ensemble"
    assert document.source_name == "wikidata_ensembles"
    assert document.name == "Vienna Philharmonic"
    assert set(document.claims) == {
        SourceClaim("ensemble_type", value="orchestra"),
        SourceClaim("based_in", "place", "Vienna", object_wikidata_id="Q1741"),
        SourceClaim("also_known_as", value="Wiener Philharmoniker"),
        SourceClaim("musicbrainz_id", value="mb-1"),
    }
    assert document.raw["based_in"] == {"item": "Q1741", "label": "Vienna"}
    assert document.raw["classes"] == [ORCHESTRA]


@pytest.mark.parametrize(
    ("classes", "label", "expected"),
    [
        ({ORCHESTRA, CHOIR}, "Choir and Orchestra", "choir"),
        ({ORCHESTRA}, "Some Orchestra", "orchestra"),
        (set(), "Hagen Quartet", "chamber_ensemble"),
        ({ORCHESTRA}, "Hagen Quartet", "orchestra"),
        (set(), "Ensemble Intercontemporain", "other"),
    ],
)
def test_the_type_comes_from_the_classes_then_the_name(classes: set[str], label: str, expected: str) -> None:
    assert ensemble_type(classes, label) == expected


@pytest.mark.parametrize(
    ("label", "names", "expected"),
    [
        # the cue is only in an alias, or in a word only the cue list knows
        ("Concentus Hungaricus", {"Concentus Hungaricus Kamarazenekar"}, "orchestra"),
        ("Staatskapelle Weimar", set(), "orchestra"),
        ("Magyar Rádió Kórusa", {"Magyar Rádió Kórus"}, "choir"),
        ("Panocha", {"Panochovo kvarteto", "Panocha Kvartet"}, "chamber_ensemble"),
        # the label's own type wins over an alias's
        ("Arnold Schoenberg Chor", {"Arnold Schoenberg Quartet"}, "choir"),
        # the most specific alias type, whatever order the names come in
        ("RIAS", {"RIAS Orchestra", "RIAS Kammerchor"}, "choir"),
        ("Collegium Vocale Gent", {"CVG"}, "other"),
    ],
)
def test_a_musical_group_is_typed_from_all_its_names(label: str, names: set[str], expected: str) -> None:
    assert ensemble_type(set(), label, names) == expected


def test_the_type_reads_the_aliases_when_the_label_has_no_cue() -> None:
    rows = [row("Q1", "Kodály"), {"item": uri("Q1"), "name": lit("Kodály Quartet")}]
    assert SourceClaim("ensemble_type", value="chamber_ensemble") in claims_of(rows)


def test_a_headquarters_that_is_a_settlement_is_the_home_city() -> None:
    rows = [row("Q1", "Orchestra", hq="Q1741=Vienna", hqSettlement="true", hqAdmin="Q40=Austria")]
    assert SourceClaim("based_in", "place", "Vienna", object_wikidata_id="Q1741") in claims_of(rows)


def test_a_headquarters_that_is_a_building_is_read_through_its_admin_area() -> None:
    rows = [row("Q1", "Orchestra", hq="Q2=Musikverein", hqSettlement="false", hqAdmin="Q1741=Vienna")]
    based_in = [c for c in claims_of(rows) if c.predicate == "based_in"]
    assert based_in == [SourceClaim("based_in", "place", "Vienna", object_wikidata_id="Q1741")]


def test_an_unnamed_home_city_is_skipped() -> None:
    rows = [row("Q1", "Orchestra", hq="Q2=Q2", hqSettlement="true")]
    assert not [c for c in claims_of(rows) if c.predicate == "based_in"]


def test_every_name_in_every_language_is_an_alias_once() -> None:
    rows = [row("Q1", "Vienna Philharmonic")] + [
        {"item": uri("Q1"), "name": lit(name)}
        for name in ("Vienna Philharmonic", "vienna philharmonic", "Wiener Philharmoniker", "VPO")
    ]
    aliases = [c.value for c in claims_of(rows) if c.predicate == "also_known_as"]
    assert aliases == ["VPO", "Wiener Philharmoniker"]


def test_part_of_is_kept_only_within_the_population() -> None:
    rows = [
        row("Q1", "Vienna State Opera Orchestra"),
        row("Q1", "Vienna State Opera Orchestra", partOf="Q2=Some Orchestra Federation"),
        row("Q1", "Vienna State Opera Orchestra", partOf="Q3=Vienna State Opera"),
    ]
    (document,) = _documents_from_rows(rows, {"Q1", "Q2"}, NOW)
    part_of = [c for c in document.claims if c.predicate == "part_of"]
    assert part_of == [
        SourceClaim("part_of", "ensemble", "Some Orchestra Federation", object_wikidata_id="Q2")
    ]
    assert [p["item"] for p in document.raw["part_of"]] == ["Q2", "Q3"]


def test_an_unlabelled_item_is_skipped() -> None:
    assert _documents_from_rows([row("Q1", "Q1")], {"Q1"}, NOW) == []


def label(qid: str, name: str, lang: str) -> dict[str, Any]:
    return {"item": uri(qid), "name": lit(name), "nameLang": lit(lang)}


def test_an_item_without_an_english_label_takes_the_first_name_language_label() -> None:
    rows = [
        row("Q1", "Q1"),
        label("Q1", "Orchestre de Budapest", "fr"),
        label("Q1", "Budapester Sinfonieorchester", "de"),
        {"item": uri("Q1"), "name": lit("BSO")},  # an alias: never the display name
    ]
    (document,) = _documents_from_rows(rows, {"Q1"}, NOW)
    assert document.name == "Budapester Sinfonieorchester"  # de before fr in NAME_LANGUAGES
    assert SourceClaim("ensemble_type", value="orchestra") in document.claims
    aliases = [c.value for c in document.claims if c.predicate == "also_known_as"]
    assert aliases == ["BSO", "Orchestre de Budapest"]


def test_an_english_label_wins_over_the_fallback() -> None:
    rows = [row("Q1", "Budapest Symphony Orchestra"), label("Q1", "Budapester Sinfonieorchester", "de")]
    (document,) = _documents_from_rows(rows, {"Q1"}, NOW)
    assert document.name == "Budapest Symphony Orchestra"


def test_an_item_with_only_aliases_is_still_skipped() -> None:
    rows = [row("Q1", "Q1"), {"item": uri("Q1"), "name": lit("BSO")}]
    assert _documents_from_rows(rows, {"Q1"}, NOW) == []


@pytest.mark.parametrize(
    ("cls", "expected"),
    [
        ("Q207338", "chamber_ensemble"),  # string quartet
        ("Q9390516", "chamber_ensemble"),
        ("Q19191604", "chamber_ensemble"),
        ("Q20819922", "opera_company"),
    ],
)
def test_the_widened_classes_type_their_instances(cls: str, expected: str) -> None:
    assert ensemble_type({cls}, "Kodály") == expected


def test_a_musical_group_is_typed_from_its_name() -> None:
    assert ensemble_type(set(), "London Philharmonic Orchestra") == "orchestra"


def test_the_queries_ask_for_the_anchoring_properties() -> None:
    assert f"wd:{ORCHESTRA}" in ENSEMBLE_ID_QUERY and f"wd:{CHOIR}" in ENSEMBLE_ID_QUERY
    assert "wd:Q207338" in ENSEMBLE_ID_QUERY and "wd:Q20819922" in ENSEMBLE_ID_QUERY
    assert "wdt:P159" in ENSEMBLE_QUERY and "wdt:P131" in ENSEMBLE_QUERY and "wdt:P434" in ENSEMBLE_QUERY
    assert "skos:altLabel" in ENSEMBLE_MULTI_QUERY and "wdt:P361" in ENSEMBLE_MULTI_QUERY
    assert "?nameLang" in ENSEMBLE_MULTI_QUERY


def test_the_musical_group_queries_need_a_classical_signal() -> None:
    for group in MUSICAL_GROUPS:
        assert f"wd:{group}" in GENRE_GROUP_ID_QUERY and f'"{group}"' in NAME_GROUP_ID_QUERY
    # only direct instances: the subclass tree is every rock band
    assert "?item wdt:P31 ?group" in GENRE_GROUP_ID_QUERY and "P279* ?group" not in GENRE_GROUP_ID_QUERY
    assert all(f"wd:{genre}" in GENRE_GROUP_ID_QUERY for genre in CLASSICAL_GENRES)
    assert "?genre wdt:P279* ?genreRoot" in GENRE_GROUP_ID_QUERY
    assert all(f'"{cue}"' in NAME_GROUP_ID_QUERY for cue in NAME_CUES)
    assert "haswbstatement:P31=" in NAME_GROUP_ID_QUERY and "inlabel:" in NAME_GROUP_ID_QUERY
    # each search reports its own hit count, so a short one is caught
    assert "/api/query/searchinfo/@totalhits" in NAME_GROUP_ID_QUERY
    # a genre outside the classical trees binds ?otherGenre; art music does not
    assert "?item wdt:P136 ?otherGenre" in NAME_GROUP_ID_QUERY
    assert all(f"wd:{genre}" in NAME_GROUP_ID_QUERY for genre in CLASSICAL_GENRES)
    assert "?otherGenre != wd:Q1583807" in NAME_GROUP_ID_QUERY
    assert {"orchestra", "philharmonic", "choir", "chor", "quartet"} <= set(NAME_CUES)
    assert "trio" not in NAME_CUES


def test_the_name_cues_are_the_typer_s_word_list() -> None:
    assert NAME_CUES == tuple(CLASSICAL_NAME_CUES)


def hit(qid: str, search: str, total: int, other_genre: str | None = None) -> dict[str, Any]:
    """A NAME_GROUP_ID_QUERY row: one search's title and the hits it reported."""
    bindings: dict[str, Any] = {"item": uri(qid), "search": lit(search), "total": lit(str(total))}
    if other_genre is not None:
        bindings["otherGenre"] = uri(other_genre)
    return bindings


def id_handler(
    answers: dict[str, list[list[dict[str, Any]]]], asked: list[str]
) -> Callable[[httpx.Request], httpx.Response]:
    """Serve each id query its next answer in turn."""

    def handler(request: httpx.Request) -> httpx.Response:
        query = urllib.parse.parse_qs(request.read().decode())["query"][0]
        asked.append(query)
        return httpx.Response(200, json={"results": {"bindings": answers[query].pop(0)}})

    return handler


@pytest.fixture
def low_floors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ensembles, "MIN_IDS", {"class": 1, "genre": 1, "name": 1})


def ids(*qids: str) -> list[dict[str, Any]]:
    return [{"item": uri(q)} for q in qids]


@pytest.mark.usefixtures("low_floors")
def test_fetch_ensemble_qids_unions_the_id_queries_and_orders_numerically() -> None:
    answers = {
        ENSEMBLE_ID_QUERY: [ids("Q100", "Q20", "Q3")],
        GENRE_GROUP_ID_QUERY: [ids("Q863397", "Q20")],
        # a name-cued group with a non-classical genre (Q8341, jazz) is dropped
        NAME_GROUP_ID_QUERY: [[hit("Q4", "a", 2), hit("Q5", "a", 2, other_genre="Q8341"), hit("Q4", "b", 1)]],
    }
    asked: list[str] = []
    with mock_session(id_handler(answers, asked)) as session:
        assert _fetch_ensemble_qids(session) == ["Q3", "Q4", "Q20", "Q100", "Q863397"]
    assert asked == [ENSEMBLE_ID_QUERY, GENRE_GROUP_ID_QUERY, NAME_GROUP_ID_QUERY]


@pytest.mark.parametrize("short", ["class", "genre", "name"])
def test_an_id_query_short_of_its_floor_fails_the_run(monkeypatch: pytest.MonkeyPatch, short: str) -> None:
    monkeypatch.setattr(ensembles, "MIN_IDS", {"class": 2, "genre": 2, "name": 2} | {short: 3})
    answers = {
        ENSEMBLE_ID_QUERY: [ids("Q1", "Q2")],
        GENRE_GROUP_ID_QUERY: [ids("Q3", "Q4")],
        NAME_GROUP_ID_QUERY: [[hit("Q5", "a", 1), hit("Q6", "b", 1)]],
    }
    with (
        mock_session(id_handler(answers, [])) as session,
        pytest.raises(RuntimeError, match="fewer than the 3"),
    ):
        _fetch_ensemble_qids(session)


@pytest.mark.usefixtures("low_floors")
def test_a_search_short_of_its_hits_is_asked_again() -> None:
    answers = {
        ENSEMBLE_ID_QUERY: [ids("Q1")],
        GENRE_GROUP_ID_QUERY: [ids("Q2")],
        NAME_GROUP_ID_QUERY: [[hit("Q3", "a", 2)], [hit("Q3", "a", 2), hit("Q4", "a", 2)]],
    }
    asked: list[str] = []
    with mock_session(id_handler(answers, asked)) as session:
        assert _fetch_ensemble_qids(session) == ["Q1", "Q2", "Q3", "Q4"]
    assert asked.count(NAME_GROUP_ID_QUERY) == 2


@pytest.mark.usefixtures("low_floors")
def test_a_search_that_stays_short_fails_the_run() -> None:
    answers = {
        ENSEMBLE_ID_QUERY: [ids("Q1")],
        GENRE_GROUP_ID_QUERY: [ids("Q2")],
        NAME_GROUP_ID_QUERY: [[hit("Q3", "a", 2)] for _ in range(ensembles.NAME_QUERY_ATTEMPTS)],
    }
    asked: list[str] = []
    with (
        mock_session(id_handler(answers, asked)) as session,
        pytest.raises(RuntimeError, match="stayed short"),
    ):
        _fetch_ensemble_qids(session)
    assert asked.count(NAME_GROUP_ID_QUERY) == ensembles.NAME_QUERY_ATTEMPTS


def test_a_short_page_fails_the_run_and_is_not_mirrored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(urllib.parse.parse_qs(request.read().decode())["query"][0])
        return httpx.Response(200, json={"results": {"bindings": [row("Q1", "A")]}})

    session = mock_session(handler, cache=PageCache(tmp_path / "pages.db"))
    for _ in range(2):
        with pytest.raises(RuntimeError, match="1 of 2 requested ensembles"):
            _fetch_ensemble_page(session, ["Q1", "Q2"])
    assert len(asked) == 2


def test_a_complete_page_is_mirrored(tmp_path: Path) -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(urllib.parse.parse_qs(request.read().decode())["query"][0])
        return httpx.Response(200, json={"results": {"bindings": [row("Q1", "A")]}})

    session = mock_session(handler, cache=PageCache(tmp_path / "pages.db"))
    for _ in range(2):
        _fetch_ensemble_page(session, ["Q1"])
    assert len(asked) == 2  # detail + multi, once each


@pytest.mark.usefixtures("low_floors")
def test_fetch_pages_through_the_population(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = urllib.parse.parse_qs(request.read().decode())["query"][0]
        if query in (ENSEMBLE_ID_QUERY, GENRE_GROUP_ID_QUERY):
            return httpx.Response(200, json={"results": {"bindings": [{"item": uri("Q1")}]}})
        if query == NAME_GROUP_ID_QUERY:
            return httpx.Response(200, json={"results": {"bindings": [hit("Q1", "a", 1)]}})
        if "?root" in query:
            return httpx.Response(200, json={"results": {"bindings": [root("Q1", CHOIR)]}})
        return httpx.Response(200, json={"results": {"bindings": [row("Q1", "Arnold Schoenberg Chor")]}})

    monkeypatch.setattr(WikidataEnsemblesAdapter, "open_session", lambda self: mock_session(handler))
    (document,) = WikidataEnsemblesAdapter().fetch()
    assert SourceClaim("ensemble_type", value="choir") in document.claims


def test_fetch_refuses_an_empty_population(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": {"bindings": []}})

    monkeypatch.setattr(WikidataEnsemblesAdapter, "open_session", lambda self: mock_session(handler))
    with pytest.raises(RuntimeError, match="ensemble id list returned 0 ids"):
        list(WikidataEnsemblesAdapter().fetch())


def test_the_adapter_is_registered() -> None:
    assert isinstance(REGISTRY["wikidata_ensembles"], WikidataEnsemblesAdapter)
