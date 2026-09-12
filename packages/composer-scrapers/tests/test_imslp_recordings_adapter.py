"""The documents the imslp_recordings adapter produces, and its registration.

HTTP is not mocked here: the page walk is replaced outright, so these tests are
about what the adapter *makes* of a page rather than how it got one.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from composer_schema import EntityDocument, WorkMentionDocument
from composer_scrapers import REGISTRY
from composer_scrapers.imslp_recordings import ImslpRecordingsAdapter

BLAKE = (
    '<script>JGCommRec=[{"aid":61682,'
    '"tit":"VAUGHAN WILLIAMS, R.: 10 Blake Songs (Banfalvi)",'
    '"img":"https://cdn.imslp.org/naxoscache.php?pool=hires&file=C5035.jpg",'
    '"art":"William Blake (lyricist), Andreas Weller (tenor), Lajos Lencs\\u00e9s (oboe)",'
    '"trs":[{"dsc":"No. 1. Infant Joy","dur":106}]}];</script>'
)

WESENDONCK = (
    '<script>JGCommRec=[{"aid":74819,'
    '"tit":"Wesendonck Lieder",'
    '"art":"Anne Schwanewilms (soprano), Vienna Radio Symphony Orchestra, '
    'Cornelius Meister (conductor)",'
    '"trs":[{"dsc":"Der Engel","dur":180}]},'
    '{"aid":74820,"tit":"Another Release","art":"Anne Schwanewilms (soprano)","trs":[]}];</script>'
)

NO_RECORDINGS = "<script>JGaskdonation=0;</script>"

PAGES: dict[int, tuple[str, str]] = {
    49198: ("10 Blake Songs (Vaughan Williams, Ralph)", BLAKE),
    117865: ("Wesendonck Lieder (Wagner, Richard)", WESENDONCK),
    999: ("Nothing Recorded (Someone, A)", NO_RECORDINGS),
}


def _walk(pages: dict[int, tuple[str, str]] | None = None) -> Any:
    chosen = PAGES if pages is None else pages

    def fake(max_pages: int | None = None) -> Iterator[tuple[int, str, str, str]]:
        for index, (pageid, (title, document)) in enumerate(chosen.items()):
            if max_pages is not None and index >= max_pages:
                return
            yield pageid, title, f"https://imslp.org/wiki/{pageid}", document

    return fake


@pytest.fixture
def documents(monkeypatch: pytest.MonkeyPatch) -> list[EntityDocument | WorkMentionDocument]:
    monkeypatch.setattr("composer_scrapers.imslp_recordings.iter_recording_pages", _walk())
    return list(ImslpRecordingsAdapter().fetch())


def _mentions(docs: list[Any]) -> list[WorkMentionDocument]:
    return [doc for doc in docs if isinstance(doc, WorkMentionDocument)]


def _people(docs: list[Any]) -> list[EntityDocument]:
    return [doc for doc in docs if isinstance(doc, EntityDocument)]


def test_imslp_recordings_is_registered() -> None:
    adapter = REGISTRY["imslp_recordings"]
    assert isinstance(adapter, ImslpRecordingsAdapter)
    assert adapter.name == "imslp_recordings"


def test_one_mention_per_album_the_work_appears_on(documents: list[Any]) -> None:
    assert [mention.id for mention in _mentions(documents)] == [
        "49198#61682",
        "117865#74819",
        "117865#74820",
    ]


def test_a_page_with_no_recordings_produces_nothing(documents: list[Any]) -> None:
    assert not [mention for mention in _mentions(documents) if mention.id.startswith("999")]


def test_the_mention_carries_the_work_and_its_composer(documents: list[Any]) -> None:
    mention = _mentions(documents)[0]
    assert mention.title == "10 Blake Songs"
    assert mention.composer == "Vaughan Williams, Ralph"


def test_the_payload_is_the_shape_derive_recordings_reads(documents: list[Any]) -> None:
    """``_recording_fields`` keys on these three and nothing else."""
    raw = _mentions(documents)[0].raw
    assert raw["_kind"] == "recording"
    assert raw["record_key"] == "61682"
    assert raw["artists"]


def test_the_record_key_is_the_album_so_a_release_folds_into_one_recording() -> None:
    """The same album on two work pages must not become two recordings."""
    pages = {
        1: ("First Work (A, B)", WESENDONCK),
        2: ("Second Work (C, D)", WESENDONCK),
    }
    fake = _walk(pages)
    adapter = ImslpRecordingsAdapter()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("composer_scrapers.imslp_recordings.iter_recording_pages", fake)
        mentions = _mentions(list(adapter.fetch()))
    assert {mention.raw["record_key"] for mention in mentions} == {"74819", "74820"}
    assert len(mentions) == 4


def test_the_catalogue_number_comes_off_the_cover(documents: list[Any]) -> None:
    assert _mentions(documents)[0].raw["catalogue_number"] == "C5035"


def test_the_tracks_are_kept_without_their_streaming_tokens(documents: list[Any]) -> None:
    assert _mentions(documents)[0].raw["tracks"] == [{"description": "No. 1. Infant Joy", "duration_s": 106}]


def test_a_lyricist_is_named_in_the_payload_but_is_not_a_participant(documents: list[Any]) -> None:
    raw = _mentions(documents)[0].raw
    assert "William Blake" in (raw["credit_line"] or "")
    assert [artist["name"] for artist in raw["artists"]] == ["Andreas Weller", "Lajos Lencsés"]


def test_a_lyricist_does_not_become_an_entity(documents: list[Any]) -> None:
    assert "William Blake" not in {person.name for person in _people(documents)}


def test_participants_carry_the_roles_the_recordings_pass_understands(documents: list[Any]) -> None:
    artists = _mentions(documents)[1].raw["artists"]
    assert [(artist["name"], artist["role"]) for artist in artists] == [
        ("Anne Schwanewilms", "soloist"),
        ("Vienna Radio Symphony Orchestra", "ensemble"),
        ("Cornelius Meister", "conductor"),
    ]


def test_people_come_after_every_mention(documents: list[Any]) -> None:
    """A profession is settled by every credit, so nobody can be emitted early."""
    kinds = [isinstance(doc, WorkMentionDocument) for doc in documents]
    assert kinds == sorted(kinds, reverse=True)


def test_an_artist_credited_twice_loads_once(documents: list[Any]) -> None:
    schwanewilms = [p for p in _people(documents) if p.name == "Anne Schwanewilms"]
    assert len(schwanewilms) == 1
    assert schwanewilms[0].raw["albums"] == 2


def test_a_performer_claims_their_profession_and_their_instrument(documents: list[Any]) -> None:
    (weller,) = [p for p in _people(documents) if p.name == "Andreas Weller"]
    assert weller.kind == "person"
    assert [(claim.predicate, claim.object_label or claim.value) for claim in weller.claims] == [
        ("has_profession", "soloist"),
        ("performs_as", "tenor"),
    ]


def test_a_conductor_is_claimed_as_one(documents: list[Any]) -> None:
    (meister,) = [p for p in _people(documents) if p.name == "Cornelius Meister"]
    assert [(claim.predicate, claim.object_label) for claim in meister.claims] == [
        ("has_profession", "conductor")
    ]


def test_an_ensemble_is_an_ensemble_and_claims_no_profession(documents: list[Any]) -> None:
    """An orchestra let through as a person would enter the person dedupe pass."""
    (orchestra,) = [p for p in _people(documents) if p.name == "Vienna Radio Symphony Orchestra"]
    assert orchestra.kind == "ensemble"
    assert orchestra.claims == ()


def test_an_artist_loads_under_a_stable_id(documents: list[Any]) -> None:
    (weller,) = [p for p in _people(documents) if p.name == "Andreas Weller"]
    assert weller.id == "/names/Andreas%20Weller"


def test_max_pages_reaches_the_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int | None] = []

    def fake(max_pages: int | None = None) -> Iterator[tuple[int, str, str, str]]:
        seen.append(max_pages)
        return iter(())

    monkeypatch.setattr("composer_scrapers.imslp_recordings.iter_recording_pages", fake)
    list(ImslpRecordingsAdapter().fetch(max_pages=5))
    assert seen == [5]
