"""Tests for the classical-music-online.net source."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from composer_scrapers import REGISTRY, EntityDocument, WorkMentionDocument
from composer_scrapers.classicalmusiconline import BASE_URL, ClassicalMusicOnlineAdapter
from composer_scrapers.classicalmusiconline.composers import (
    index_record,
    iter_index_entries,
    parse_life_years,
)
from composer_scrapers.classicalmusiconline.fetch import iter_composers
from composer_scrapers.classicalmusiconline.works import iter_work_mentions

# Trimmed copy of a real index page: the "Notable Composers" box that repeats
# composers as bare links, a living composer ("born 1970"), a row with life
# years and a country, a vaguely dated row ("16??-17??"), a roman-numeral
# century range, a country-only row, and a nameless-given-name row
# ("Anonymous,") that carries neither dates nor country.
INDEX_PAGE = """
<fieldset class="catalog_fieldset"><legend>Notable Composers</legend>
<a href="/en/composer/Albinoni/785" style="margin-right:20px;">T. Albinoni</a>
</fieldset>
<tr class="for_search" id="aa michel van der">
<td colspan="2"><a href="/en/composer/Aa/1273" style="font-size:15px;">
Aa, Michel van der
 <span style="font-size:12px;"> (born 1970)</span> <span style="font-size:12px;">(Netherlands)</span></a>
</td></tr>
<tr class="for_search" id="albinoni tommazo">
<td colspan="2"><a href="/en/composer/Albinoni/785">
Albinoni, Tommazo <span>(1671-1751)</span> <span>(Italy)</span></a>
</td></tr>
<tr class="for_search" id="anonymous">
<td colspan="2"><a href="/en/composer/Anonymous/4321">
Anonymous, </a>
</td></tr>
<tr class="for_search" id="vague">
<td colspan="2"><a href="/en/composer/Vague/999">
Vague, Jean <span>(16??-17??)</span> <span>(France)</span></a>
</td></tr>
<tr class="for_search" id="medieval">
<td colspan="2"><a href="/en/composer/Medieval/888">
Medieval, Anon <span>(XVI&#0178;-XVII)</span> <span>(Germany)</span></a>
</td></tr>
<tr class="for_search" id="countryonly">
<td colspan="2"><a href="/en/composer/Country/777">
Country, Only <span>(Ukraine)</span></a>
</td></tr>
"""

# Trimmed copy of a real composer page: a work with an opus, one without, and
# one whose title cell has no production link.
COMPOSER_PAGE = """
<div id="comp_249" rel="comp">
<table class="prdList">
<tr class="result" id="24 piano pieces">
<td class="prdName"><a href="/en/production/1060">24 Piano Pieces (1894)</a>
<img src="/files/img/audio.png" title="Audio" /></td>
<td class="prdOpus"><a href="/en/production/1060" style="font-size:15px !important;">op. 36</a></td>
</tr>
<tr class="result" id="adagio">
<td class="prdName"><a href="/en/production/1629">Adagio in g-moll</a></td>
<td class="prdOpus"><a href="/en/production/1629" style="font-size:15px !important;"></a></td>
</tr>
<tr class="result" id="unlinked">
<td class="prdName">Egyptian Nights &#0150; ballet</td>
<td class="prdOpus"></td>
</tr>
</table>
</div>
"""


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    class _MockedClient(httpx.Client):
        def __init__(self, **kw: Any) -> None:
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(**kw)

    monkeypatch.setattr("composer_scrapers.classicalmusiconline.fetch.httpx.Client", _MockedClient)
    monkeypatch.setattr("composer_scrapers.classicalmusiconline.fetch.time.sleep", lambda _: None)
    monkeypatch.setattr("composer_scrapers._http.time.sleep", lambda _: None)


# ---------------------------------------------------------------------------
# index parsing
# ---------------------------------------------------------------------------


def test_index_entries_skip_the_notable_composers_box() -> None:
    entries = iter_index_entries(INDEX_PAGE, BASE_URL, "A")
    assert [entry.external_id for entry in entries] == ["1273", "785", "4321", "999", "888", "777"]


def test_index_entry_splits_name_dates_and_country() -> None:
    entry = iter_index_entries(INDEX_PAGE, BASE_URL, "A")[0]
    assert entry.name == "Aa, Michel van der"
    assert entry.dates == "born 1970"
    assert entry.country == "Netherlands"
    assert entry.url == BASE_URL + "/en/composer/Aa/1273"
    assert entry.letter == "A"


def test_index_entry_without_dates_or_country() -> None:
    entry = iter_index_entries(INDEX_PAGE)[2]
    assert entry.name == "Anonymous"
    assert (entry.dates, entry.country) == ("", "")


def test_index_entry_keeps_country_when_dates_are_a_century_range() -> None:
    entry = iter_index_entries(INDEX_PAGE)[4]
    assert entry.dates == "XVI\xb2-XVII"
    assert entry.country == "Germany"


def test_index_entry_with_country_but_no_dates() -> None:
    entry = iter_index_entries(INDEX_PAGE)[5]
    assert (entry.dates, entry.country) == ("", "Ukraine")


@pytest.mark.parametrize(
    ("dates", "expected"),
    [
        ("born 1970", ("1970", None)),
        ("1671-1751", ("1671", "1751")),
        ("840-912", ("840", "912")),
        ("16??-17??", (None, None)),
        ("18__-1923", (None, "1923")),
        ("1764--?", ("1764", None)),
        ("born 19__", (None, None)),
        ("XVI-XVII", (None, None)),
        ("", (None, None)),
    ],
)
def test_parse_life_years(dates: str, expected: tuple[str | None, str | None]) -> None:
    assert parse_life_years(dates) == expected


def test_index_record_claims() -> None:
    record = index_record(iter_index_entries(INDEX_PAGE, BASE_URL, "A")[1])
    assert record.external_id == "785"
    assert record.name == "Albinoni, Tommazo"
    assert record.url == BASE_URL + "/en/composer/Albinoni/785"
    assert [(claim.predicate, claim.object_label or claim.value) for claim in record.claims] == [
        ("has_profession", "composer"),
        ("born_on", "1671"),
        ("died_on", "1751"),
        ("citizen_of", "Italy"),
    ]
    assert record.raw["dates"] == "1671-1751"
    assert record.raw["letter"] == "A"


def test_index_record_omits_vague_years_but_keeps_them_raw() -> None:
    record = index_record(iter_index_entries(INDEX_PAGE)[3])
    assert [claim.predicate for claim in record.claims] == ["has_profession", "citizen_of"]
    assert record.raw["dates"] == "16??-17??"


# ---------------------------------------------------------------------------
# works parsing
# ---------------------------------------------------------------------------


def test_work_mention_keeps_the_opus_out_of_the_title() -> None:
    # folding the opus in makes the matcher auto-merge distinct works that share
    # an opus number, which a whole-catalogue source produces constantly
    mention = iter_work_mentions(COMPOSER_PAGE, "Arensky, Anton", "249", BASE_URL)[0]
    assert mention.external_id == "1060"
    assert mention.title == "24 Piano Pieces (1894)"
    assert mention.composer == "Arensky, Anton"
    assert mention.raw["title"] == "24 Piano Pieces (1894)"
    assert mention.raw["opus"] == "op. 36"
    assert mention.raw["url"] == BASE_URL + "/en/production/1060"


def test_work_mention_without_opus() -> None:
    mention = iter_work_mentions(COMPOSER_PAGE, "Arensky, Anton", "249")[1]
    assert mention.title == "Adagio in g-moll"
    assert mention.raw["opus"] is None


def test_work_mention_without_production_link_gets_a_stable_id() -> None:
    first = iter_work_mentions(COMPOSER_PAGE, "Arensky, Anton", "249")[2]
    second = iter_work_mentions(COMPOSER_PAGE, "Arensky, Anton", "249")[2]
    assert first.title == "Egyptian Nights – ballet"
    assert first.raw["production_id"] is None
    assert first.external_id == second.external_id
    assert first.external_id != "1060"


def test_work_mentions_ignore_pages_without_a_works_table() -> None:
    assert iter_work_mentions("<div id='comp_1'></div>", "Nobody", "1") == []


# ---------------------------------------------------------------------------
# crawl
# ---------------------------------------------------------------------------


def _handler(requests: list[str], detail_status: int = 200) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requests.append(url)
        if "/en/composers/" in url:
            # only letter A lists anyone; the rest are empty pages
            return httpx.Response(200, text=INDEX_PAGE if url.endswith("/A") else "")
        return httpx.Response(detail_status, text=COMPOSER_PAGE)

    return handle


def test_iter_composers_walks_the_alphabet_and_fetches_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[str] = []
    _patch_client(monkeypatch, _handler(requests))

    results = list(iter_composers())

    assert [entry.external_id for entry, _ in results] == ["1273", "785", "4321", "999", "888", "777"]
    assert all(page == COMPOSER_PAGE for _, page in results)
    index_requests = [url for url in requests if "/en/composers/" in url]
    assert len(index_requests) == 26
    assert index_requests[0].endswith("/A") and index_requests[-1].endswith("/Z")


def test_iter_composers_caps_detail_fetches_with_max_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[str] = []
    _patch_client(monkeypatch, _handler(requests))

    results = list(iter_composers(max_pages=2))

    assert len(results) == 2
    # stops inside letter A, so the remaining index pages are never fetched
    assert [url for url in requests if "/en/composers/" in url] == [BASE_URL + "/en/composers/A"]


def test_iter_composers_skips_a_composer_page_that_keeps_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[str] = []
    _patch_client(monkeypatch, _handler(requests, detail_status=500))

    assert list(iter_composers()) == []


def test_iter_composers_visits_each_composer_once(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requests.append(url)
        if "/en/composers/" in url:
            # the same composer is listed under two letters
            return httpx.Response(200, text=INDEX_PAGE if url[-1] in "AB" else "")
        return httpx.Response(200, text=COMPOSER_PAGE)

    _patch_client(monkeypatch, handle)

    results = list(iter_composers())

    assert len(results) == 6
    assert len([url for url in requests if "/en/composer/" in url]) == 6


# ---------------------------------------------------------------------------
# adapter
# ---------------------------------------------------------------------------


def test_adapter_yields_each_composer_then_its_works(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[str] = []
    _patch_client(monkeypatch, _handler(requests))

    docs = list(ClassicalMusicOnlineAdapter().fetch(max_pages=1))

    assert isinstance(docs[0], EntityDocument)
    assert docs[0].name == "Aa, Michel van der"
    assert docs[0].source_name == "classicalmusiconline"
    mentions = [doc for doc in docs[1:] if isinstance(doc, WorkMentionDocument)]
    assert len(mentions) == len(docs) - 1
    assert [doc.title for doc in mentions] == [
        "24 Piano Pieces (1894)",
        "Adagio in g-moll",
        "Egyptian Nights – ballet",
    ]
    assert all(doc.composer == "Aa, Michel van der" for doc in mentions)


def test_adapter_is_registered() -> None:
    assert isinstance(REGISTRY["classicalmusiconline"], ClassicalMusicOnlineAdapter)
    assert REGISTRY["classicalmusiconline"].name == "classicalmusiconline"
    assert REGISTRY["classicalmusiconline"].base_url == BASE_URL
