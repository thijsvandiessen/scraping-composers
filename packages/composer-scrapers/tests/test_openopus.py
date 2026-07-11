"""Tests for the Open Opus work dump source."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from composer_scrapers import EntityDocument, RefreshCadence, WorkMentionDocument
from composer_scrapers.openopus import OpenOpusAdapter, _year
from composer_scrapers.openopus.fetch import _fetch_dump, _make_client

# A dump excerpt: one dead composer with two works, one living composer
# (death: null) whose work carries a subtitle.
_DUMP: dict[str, Any] = {
    "composers": [
        {
            "id": "87",
            "name": "Bach",
            "complete_name": "Johann Sebastian Bach",
            "birth": "1685-01-01",
            "death": "1750-01-01",
            "epoch": "Baroque",
            "portrait": "https://assets.openopus.org/portraits/12091447-1568084857.jpg",
            "works": [
                {
                    "id": "20090",
                    "title": "Brandenburg Concerto No. 1 in F major, BWV 1046",
                    "subtitle": "",
                    "searchterms": "brandenburg",
                    "popular": "1",
                    "recommended": "1",
                    "genre": "Orchestral",
                },
                {
                    "id": "20232",
                    "title": "Mass in B minor, BWV 232",
                    "subtitle": "",
                    "searchterms": "",
                    "popular": "1",
                    "recommended": "1",
                    "genre": "Vocal",
                },
            ],
        },
        {
            "id": 129,
            "name": "Adams",
            "complete_name": "John Adams",
            "birth": "1947-01-01",
            "death": None,
            "epoch": "Post-War",
            "portrait": None,
            "works": [
                {
                    "id": 18443,
                    "title": "Shaker Loops",
                    "subtitle": "for string septet",
                    "searchterms": "",
                    "popular": "0",
                    "recommended": "1",
                    "genre": "Chamber",
                },
            ],
        },
    ]
}


def _patch_dump(monkeypatch: pytest.MonkeyPatch, dump: dict[str, Any]) -> None:
    """Serve ``dump`` from a mocked HTTP transport under the adapter."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps(dump))

    class _MockedClient(httpx.Client):
        def __init__(self, **kw: Any) -> None:
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(**kw)

    monkeypatch.setattr("composer_scrapers.openopus.fetch.httpx.Client", _MockedClient)


def _fetch_all(
    monkeypatch: pytest.MonkeyPatch,
    dump: dict[str, Any],
    max_pages: int | None = None,
) -> list[EntityDocument | WorkMentionDocument]:
    _patch_dump(monkeypatch, dump)
    return list(OpenOpusAdapter().fetch(max_pages=max_pages))


# ---------------------------------------------------------------------------
# _year
# ---------------------------------------------------------------------------


def test_year_strips_january_first_padding() -> None:
    assert _year("1685-01-01") == "1685"


def test_year_keeps_genuine_dates() -> None:
    assert _year("1750-07-28") == "1750-07-28"


def test_year_handles_missing_values() -> None:
    assert _year(None) is None
    assert _year("") is None


# ---------------------------------------------------------------------------
# _fetch_dump
# ---------------------------------------------------------------------------


def test_fetch_dump_returns_composers(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_dump(monkeypatch, _DUMP)
    with _make_client() as client:
        composers = _fetch_dump(client)
    assert [c["name"] for c in composers] == ["Bach", "Adams"]


def test_fetch_dump_rejects_payload_without_composers(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_dump(monkeypatch, {"status": {"success": "false"}})
    with _make_client() as client:
        with pytest.raises(ValueError, match="composers"):
            _fetch_dump(client)


def test_fetch_dump_retries_on_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_scrapers._http.time.sleep", lambda _: None)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503, text="error")
        return httpx.Response(200, text=json.dumps(_DUMP))

    class _MockedClient(httpx.Client):
        def __init__(self, **kw: Any) -> None:
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(**kw)

    monkeypatch.setattr("composer_scrapers.openopus.fetch.httpx.Client", _MockedClient)
    with _make_client() as client:
        composers = _fetch_dump(client)
    assert len(attempts) == 3
    assert len(composers) == 2


# ---------------------------------------------------------------------------
# OpenOpusAdapter.fetch — composers
# ---------------------------------------------------------------------------


def test_fetch_yields_person_records(monkeypatch: pytest.MonkeyPatch) -> None:
    people = [d for d in _fetch_all(monkeypatch, _DUMP) if isinstance(d, EntityDocument)]
    assert [p.name for p in people] == ["Johann Sebastian Bach", "John Adams"]
    bach = people[0]
    assert bach.id == "composer:87"
    assert bach.kind == "person"
    assert bach.source_name == "openopus"


def test_fetch_attaches_composer_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    bach = next(d for d in _fetch_all(monkeypatch, _DUMP) if isinstance(d, EntityDocument))
    claims = {(c.predicate, c.object_kind, c.object_label, c.value) for c in bach.claims}
    assert claims == {
        ("has_profession", "profession", "composer", None),
        ("born_on", None, None, "1685"),
        ("died_on", None, None, "1750"),
        ("associated_period", "period", "Baroque", None),
    }


def test_fetch_omits_died_on_for_living_composers(monkeypatch: pytest.MonkeyPatch) -> None:
    adams = [d for d in _fetch_all(monkeypatch, _DUMP) if isinstance(d, EntityDocument)][1]
    predicates = [c.predicate for c in adams.claims]
    assert "died_on" not in predicates
    assert "born_on" in predicates


def test_fetch_strips_works_from_composer_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    bach = next(d for d in _fetch_all(monkeypatch, _DUMP) if isinstance(d, EntityDocument))
    assert "works" not in bach.raw
    assert bach.raw["epoch"] == "Baroque"
    assert bach.raw["portrait"] == "https://assets.openopus.org/portraits/12091447-1568084857.jpg"


def test_fetch_skips_composers_without_name_or_id(monkeypatch: pytest.MonkeyPatch) -> None:
    dump = {
        "composers": [
            {"id": "1", "complete_name": "", "name": "", "works": []},
            {"complete_name": "No Id", "works": []},
            {"id": "2", "complete_name": "Kept Composer", "works": []},
        ]
    }
    docs = _fetch_all(monkeypatch, dump)
    assert [d.name for d in docs if isinstance(d, EntityDocument)] == ["Kept Composer"]


def test_fetch_falls_back_to_short_name(monkeypatch: pytest.MonkeyPatch) -> None:
    dump = {"composers": [{"id": "3", "name": "Anonymous", "works": []}]}
    (doc,) = _fetch_all(monkeypatch, dump)
    assert isinstance(doc, EntityDocument)
    assert doc.name == "Anonymous"


def test_fetch_max_pages_caps_composers(monkeypatch: pytest.MonkeyPatch) -> None:
    docs = _fetch_all(monkeypatch, _DUMP, max_pages=1)
    people = [d for d in docs if isinstance(d, EntityDocument)]
    mentions = [d for d in docs if isinstance(d, WorkMentionDocument)]
    assert [p.name for p in people] == ["Johann Sebastian Bach"]
    assert all(m.composer == "Johann Sebastian Bach" for m in mentions)


# ---------------------------------------------------------------------------
# OpenOpusAdapter.fetch — work mentions
# ---------------------------------------------------------------------------


def test_fetch_yields_work_mentions(monkeypatch: pytest.MonkeyPatch) -> None:
    mentions = [d for d in _fetch_all(monkeypatch, _DUMP) if isinstance(d, WorkMentionDocument)]
    assert len(mentions) == 3
    first = mentions[0]
    assert first.id == "work:20090"
    assert first.title == "Brandenburg Concerto No. 1 in F major, BWV 1046"
    assert first.composer == "Johann Sebastian Bach"
    assert first.source_name == "openopus"


def test_work_mention_raw_keeps_catalogue_context(monkeypatch: pytest.MonkeyPatch) -> None:
    shaker = [d for d in _fetch_all(monkeypatch, _DUMP) if isinstance(d, WorkMentionDocument)][-1]
    assert shaker.raw["subtitle"] == "for string septet"
    assert shaker.raw["genre"] == "Chamber"
    assert shaker.raw["recommended"] == "1"
    assert shaker.raw["composer_id"] == "129"
    assert shaker.raw["epoch"] == "Post-War"


def test_fetch_skips_works_without_title_or_id(monkeypatch: pytest.MonkeyPatch) -> None:
    dump = {
        "composers": [
            {
                "id": "4",
                "complete_name": "Test Composer",
                "works": [
                    {"id": "10", "title": ""},
                    {"title": "No Id Work"},
                    {"id": "11", "title": "Kept Work"},
                ],
            }
        ]
    }
    mentions = [d for d in _fetch_all(monkeypatch, dump) if isinstance(d, WorkMentionDocument)]
    assert [m.title for m in mentions] == ["Kept Work"]


def test_fetch_handles_missing_works_list(monkeypatch: pytest.MonkeyPatch) -> None:
    dump = {"composers": [{"id": "5", "complete_name": "Workless Composer"}]}
    docs = _fetch_all(monkeypatch, dump)
    assert len(docs) == 1
    assert isinstance(docs[0], EntityDocument)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_adapter_registered_in_registry() -> None:
    from composer_scrapers import REGISTRY

    assert "openopus" in REGISTRY
    assert isinstance(REGISTRY["openopus"], OpenOpusAdapter)


def test_adapter_cadence_is_yearly() -> None:
    assert OpenOpusAdapter.cadence is RefreshCadence.YEARLY
