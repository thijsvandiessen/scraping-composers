"""Tests for reading a Decca product family payload.

The payloads here are trimmed from real GraphQL responses, keeping the quirk
each test names. Composer attribution is what most of them are about: it is the
one thing this source is read structurally *for*, and the field that looks like
it should answer it does not.
"""

from __future__ import annotations

from typing import Any

from composer_scrapers.decca.products import recordings, split_heading
from composer_scrapers.decca.urls import artist_slugs, families

# --------------------------------------------------------------------------- #
# payload builders
# --------------------------------------------------------------------------- #


def _work(work_id: int, name: str, level: int = 1) -> dict[str, Any]:
    return {"node": {"idRaw": work_id, "name": name, "level": level}}


def _artist(artist_id: int, name: str, is_composer: bool = False) -> dict[str, Any]:
    return {
        "idRaw": artist_id,
        "screenname": name,
        "isComposer": is_composer,
        "urlAlias": None,
    }


def _track(
    title: str,
    *,
    works: list[dict[str, Any]] | None = None,
    contributors: list[dict[str, Any]] | None = None,
    headings: list[dict[str, Any]] | None = None,
    number: int = 1,
    **extra: Any,
) -> dict[str, Any]:
    track: dict[str, Any] = {
        "titleNumber": number,
        "title": title,
        "works": {"edges": works if works is not None else [_work(1, "A Work")]},
        "contributors": contributors or [],
        "headings": headings or [],
    }
    track.update(extra)
    return track


def _family(
    tracks: list[dict[str, Any]],
    *,
    family_id: int = 7,
    product_id: int = 900,
    carriers: list[dict[str, Any]] | None = None,
    **product: Any,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "idRaw": product_id,
        "sku": "00028947756415",
        "label": "Deutsche Grammophon (DG)",
        "configuration": "CD album",
        "productCategory": {"name": "CD"},
        "playlist": {"carriers": carriers or [{"name": "CD 1", "tracks": tracks}]},
    }
    node.update(product)
    return {
        "idRaw": family_id,
        "headline": "A Release",
        "releaseDate": "2006-03-14",
        "products": {"edges": [{"node": node}]},
    }


def _only(family: dict[str, Any]) -> Any:
    return next(iter(recordings(family, "a-slug-7")))


# --------------------------------------------------------------------------- #
# composer attribution
# --------------------------------------------------------------------------- #


def test_the_composer_function_names_the_composer() -> None:
    recording = _only(
        _family(
            [
                _track(
                    "Overture",
                    contributors=[
                        {"function": "Composer", "artist": _artist(1, "Giuseppe Verdi", True)},
                        {"function": "Conductor", "artist": _artist(2, "Herbert von Karajan")},
                    ],
                )
            ]
        )
    )
    assert recording.mentions[0].composer == "Giuseppe Verdi"


def test_the_is_composer_flag_breaks_a_tie_between_two_composer_credits() -> None:
    """Verdi and his librettist are both filed under ``function: "Composer"``;
    only Verdi is flagged, and that is the whole of the difference."""
    recording = _only(
        _family(
            [
                _track(
                    "Overture",
                    contributors=[
                        {"function": "Composer", "artist": _artist(3, "Antonio Somma", False)},
                        {"function": "Composer", "artist": _artist(1, "Giuseppe Verdi", True)},
                    ],
                )
            ]
        )
    )
    mention = recording.mentions[0]
    assert mention.composer == "Giuseppe Verdi"
    assert mention.composers == ("Giuseppe Verdi",)


def test_an_unflagged_composer_is_still_a_composer() -> None:
    """``isComposer`` marks artists the label promotes, not artists who compose:
    it is false for John Williams. Filtering on it drops attribution to 63%."""
    recording = _only(
        _family(
            [
                _track(
                    "Hedwig's Theme",
                    contributors=[
                        {"function": "Composer", "artist": _artist(4, "John Williams", False)},
                        {"function": "Orchestra", "artist": _artist(5, "London Symphony Orchestra")},
                    ],
                )
            ]
        )
    )
    assert recording.mentions[0].composer == "John Williams"


def test_two_unflagged_composer_credits_are_both_kept() -> None:
    """A genuine two-composer track must not become no composers."""
    recording = _only(
        _family(
            [
                _track(
                    "Duet",
                    contributors=[
                        {"function": "Composer", "artist": _artist(6, "Ruggiero Leoncavallo")},
                        {"function": "Composer", "artist": _artist(7, "Giacomo Puccini")},
                    ],
                )
            ]
        )
    )
    assert set(recording.mentions[0].composers) == {"Ruggiero Leoncavallo", "Giacomo Puccini"}


def test_a_composer_author_function_counts_as_a_composer_credit() -> None:
    recording = _only(
        _family(
            [
                _track(
                    "Out to Sea",
                    contributors=[{"function": "Composer/Author", "artist": _artist(8, "John Williams")}],
                )
            ]
        )
    )
    assert recording.mentions[0].composer == "John Williams"


def test_the_heading_supplies_a_composer_when_no_credit_does() -> None:
    """13% of tracks carry no Composer-function credit at all."""
    recording = _only(
        _family(
            [
                _track(
                    "Gymnopédie no. 1",
                    contributors=[{"function": "Piano", "artist": _artist(9, "Aldo Ciccolini")}],
                    headings=[{"type": "COMPOSER", "name": "Erik Satie"}],
                )
            ]
        )
    )
    assert recording.mentions[0].composer == "Erik Satie"


def test_a_heading_echoing_the_performer_is_not_a_composer_credit() -> None:
    """A spoken-word track lists its speaker under COMPOSER; she did not write it."""
    recording = _only(
        _family(
            [
                _track(
                    "What the Edinburgh Festival Has Meant To Me",
                    contributors=[{"function": "Actor", "artist": _artist(10, "Kathleen Ferrier")}],
                    headings=[{"type": "COMPOSER", "name": "Kathleen Ferrier"}],
                )
            ]
        )
    )
    assert recording.mentions[0].composer is None


def test_a_placeholder_name_is_not_a_composer() -> None:
    recording = _only(
        _family(
            [
                _track(
                    "Greensleeves",
                    contributors=[{"function": "Composer", "artist": _artist(11, "Traditional")}],
                    headings=[{"type": "COMPOSER", "name": "Anonymous"}],
                )
            ]
        )
    )
    assert recording.mentions[0].composer is None


def test_a_work_takes_the_composer_its_tracks_mostly_agree_on() -> None:
    tracks = [
        _track(
            "I",
            number=1,
            contributors=[{"function": "Composer", "artist": _artist(1, "Johannes Brahms")}],
        ),
        _track(
            "II",
            number=2,
            contributors=[{"function": "Composer", "artist": _artist(1, "Johannes Brahms")}],
        ),
        _track("III", number=3, headings=[{"type": "COMPOSER", "name": "Anonymous arranger"}]),
    ]
    recording = _only(_family(tracks))
    mention = recording.mentions[0]
    assert mention.composer == "Johannes Brahms"
    assert "Anonymous arranger" in mention.composers


# --------------------------------------------------------------------------- #
# headings
# --------------------------------------------------------------------------- #


def test_split_heading_separates_the_people_a_comma_joins() -> None:
    assert split_heading("Alfredo Catalani, Luigi Illica") == [
        ("Alfredo Catalani", None, None),
        ("Luigi Illica", None, None),
    ]


def test_split_heading_reads_life_dates() -> None:
    assert split_heading("Benjamin Britten (1913 - 1976)") == [("Benjamin Britten", "1913", "1976")]


def test_split_heading_reads_an_open_ended_life_span() -> None:
    assert split_heading("André Previn (1929 - )") == [("André Previn", "1929", None)]


def test_split_heading_keeps_only_the_first_transliteration() -> None:
    """A slash separates spellings of the same list, not different people."""
    heading = "Anonymous, Joachim A.C. Zarnack (--)/Ernest Anschutz, Joachim August Zarnack"
    assert split_heading(heading) == [("Anonymous", None, None), ("Joachim A.C. Zarnack", None, None)]


# --------------------------------------------------------------------------- #
# structure
# --------------------------------------------------------------------------- #


def test_an_opera_is_one_mention_not_one_per_track() -> None:
    """Level 1 is the work; deeper levels are its acts and numbers."""
    tracks = [
        _track(
            f"Scene {n}",
            number=n,
            works=[
                _work(449306, "Un ballo in maschera", level=1),
                _work(551435, "ZWEITER AKT", level=2),
                _work(761790 + n, f"Scene {n}", level=3),
            ],
            contributors=[{"function": "Composer", "artist": _artist(1, "Giuseppe Verdi", True)}],
        )
        for n in range(1, 27)
    ]
    recording = _only(_family(tracks))
    assert len(recording.mentions) == 1
    mention = recording.mentions[0]
    assert mention.work_id == 449306
    assert mention.title == "Un ballo in maschera"
    assert len(mention.tracks) == 26


def test_every_carrier_contributes_its_tracks() -> None:
    """The rendered page only ever shows disc 1; the API returns them all."""
    carriers = [
        {"name": "CD 1", "tracks": [_track("Overture", number=1)]},
        {"name": "CD 2", "tracks": [_track("Act II", number=2)]},
    ]
    recording = _only(_family([], carriers=carriers))
    assert sorted(t.carrier for t in recording.mentions[0].tracks) == ["CD 1", "CD 2"]


def test_a_product_is_one_recording_keyed_by_its_own_id() -> None:
    recording = _only(_family([_track("Overture")], product_id=95920))
    assert recording.record_key == "product:95920"
    assert recording.catalogue_number == "00028947756415"
    assert recording.url == "https://www.deccaclassics.com/en/catalogue/products/a-slug-7"


def test_a_family_whose_editions_were_all_withdrawn_yields_nothing() -> None:
    family = {"idRaw": 4612, "headline": "Gone", "products": {"edges": []}}
    assert list(recordings(family, "gone-4612")) == []


def test_a_track_naming_no_work_is_skipped() -> None:
    assert list(recordings(_family([_track("Untitled", works=[])]), "s-7")) == []


# --------------------------------------------------------------------------- #
# credits
# --------------------------------------------------------------------------- #


def test_credits_become_participants_with_roles() -> None:
    recording = _only(
        _family(
            [
                _track(
                    "Overture",
                    contributors=[
                        {"function": "Conductor", "artist": _artist(2, "Herbert von Karajan")},
                        {"function": "Orchestra", "artist": _artist(3, "Wiener Philharmoniker")},
                        {"function": "Soprano", "artist": _artist(4, "Sumi Jo")},
                    ],
                )
            ]
        )
    )
    assert {(c.name, c.role) for c in recording.participants} == {
        ("Herbert von Karajan", "conductor"),
        ("Wiener Philharmoniker", "ensemble"),
        ("Sumi Jo", "soloist"),
    }


def test_an_ensemble_is_recognised_by_its_name_not_its_function() -> None:
    """The catalogue files orchestras under vague functions like "Artist"; the
    shared ``resolve_entity_kind`` knows the name is an ensemble regardless."""
    recording = _only(
        _family(
            [
                _track(
                    "Kyrie",
                    contributors=[{"function": "Artist", "artist": _artist(5, "Wiener Philharmoniker")}],
                )
            ]
        )
    )
    credit = recording.participants[0]
    assert credit.kind == "ensemble"
    assert credit.role == "ensemble"


def test_production_credits_are_kept_apart_from_performers() -> None:
    recording = _only(
        _family(
            [
                _track(
                    "Overture",
                    contributors=[
                        {"function": "Balance Engineer", "artist": _artist(6, "Günter Hermanns")},
                        {"function": "Piano", "artist": _artist(7, "Alfred Brendel")},
                    ],
                )
            ]
        )
    )
    assert [c.name for c in recording.participants] == ["Alfred Brendel"]
    assert [c.name for c in recording.production] == ["Günter Hermanns"]


def test_a_chorus_master_is_neither_a_performer_nor_an_ensemble() -> None:
    """The keyword match would otherwise read "Chorus Master" as an ensemble."""
    recording = _only(
        _family(
            [
                _track(
                    "Chorus",
                    contributors=[{"function": "Chorus Master", "artist": _artist(8, "Walter Hagen")}],
                )
            ]
        )
    )
    assert recording.participants == ()


def test_a_composer_is_not_a_performer_on_their_own_recording() -> None:
    recording = _only(
        _family(
            [
                _track(
                    "Overture",
                    contributors=[
                        {"function": "Composer", "artist": _artist(1, "Giuseppe Verdi", True)},
                        {"function": "Author", "artist": _artist(3, "Antonio Somma")},
                    ],
                )
            ]
        )
    )
    assert recording.participants == ()
    assert [c.name for c in recording.composers] == ["Giuseppe Verdi"]


# --------------------------------------------------------------------------- #
# track detail
# --------------------------------------------------------------------------- #


def test_a_partial_recording_date_stays_partial() -> None:
    """A guessed day would be indistinguishable from a stated one downstream."""
    family = _family(
        [
            _track("Year only", number=1, recordingDateYear=1989),
            _track("Year and month", number=2, recordingDateYear=1989, recordingDateMonth=2),
            _track(
                "Full",
                number=3,
                recordingDateYear=1989,
                recordingDateMonth=2,
                recordingDateDay=3,
            ),
        ]
    )
    dates = [t.recorded_on for t in _only(family).mentions[0].tracks]
    assert dates == ["1989", "1989-02", "1989-02-03"]


def test_track_detail_survives_into_the_payload() -> None:
    family = _family(
        [
            _track(
                "Overture",
                isrc="DEF058930001",
                duration=279,
                recordingLocation="Grosser Saal, Musikverein, Wien",
                recordingDateYear=1989,
            )
        ]
    )
    track = _only(family).mentions[0].tracks[0].as_raw()
    assert track["isrc"] == "DEF058930001"
    assert track["duration"] == 279
    assert track["recording_location"] == "Grosser Saal, Musikverein, Wien"


# --------------------------------------------------------------------------- #
# the sitemap
# --------------------------------------------------------------------------- #

_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset>
  <url><loc>https://www.deccaclassics.com/de/katalog/produkte/verdi-un-ballo-in-maschera-karajan-7</loc></url>
  <url><loc>https://www.deccaclassics.com/de/katalog/produkte/chopin-etudes-ashkenazy-1488</loc></url>
  <url><loc>https://www.deccaclassics.com/de/katalog/produkte/no-trailing-id</loc></url>
  <url><loc>https://www.deccaclassics.com/de/kuenstler-innen/von-karajan</loc></url>
  <url><loc>https://www.deccaclassics.com/en/artists/von-karajan</loc></url>
  <url><loc>https://www.deccaclassics.com/en/artists/alfredbrendel</loc></url>
  <url><loc>https://example.com/de/katalog/produkte/elsewhere-1</loc></url>
</urlset>
"""


def test_the_sitemap_yields_family_ids_with_their_slugs() -> None:
    assert families(_SITEMAP) == {
        7: "verdi-un-ballo-in-maschera-karajan-7",
        1488: "chopin-etudes-ashkenazy-1488",
    }


def test_the_german_and_english_spellings_of_one_artist_collapse() -> None:
    assert artist_slugs(_SITEMAP) == ["von-karajan", "alfredbrendel"]
