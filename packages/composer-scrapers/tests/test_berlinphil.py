"""Tests for parsing Digital Concert Hall concert payloads into records."""

from composer_scrapers import SourceClaim, SourceRecord, SourceWorkMention
from composer_scrapers.berlinphil.artists import _Artist, _artist_record, _collect, _register
from composer_scrapers.berlinphil.performances import _performances

# Trimmed copies of the real v2/concert/{id} structure: a concert with an
# orchestra + conductor at concert level, works whose composer and soloists are
# tagged by role.type in _links.artist, per-work conductor/orchestra name lists,
# an epoch (period), an encore, and a work with no soloist. The same artist
# (Daniel Barenboim) appears in two concerts so the registry must dedup him.
CONCERTS = [
    {
        "id": "56434",
        "date": {"begin": 1780765200},  # 2026-06-06, Europe/Berlin
        "_links": {
            "season": [{"label": "2025/26"}],
            "artist": [
                {
                    "id": "1",
                    "name": "Berliner Philharmoniker",
                    "display_type": "group",
                    "group_name": "Berliner Philharmoniker",
                    "fields_of_work": [],
                    "role": {"type": "group", "name": "orchestra"},
                },
                {
                    "id": "1562",
                    "name": "Jakub Hrůša",
                    "display_type": "person",
                    "fields_of_work": ["conductor"],
                    "role": {"type": "conductor", "name": "conductor"},
                },
            ],
        },
        "_embedded": {
            "work": [
                {
                    "id": "56434-1",
                    "title": "[Suita rustica], op. 19",
                    "is_encore": False,
                    "name_composers": ["Vítězslava Kaprálová"],
                    "name_conductor": ["Jakub Hrůša"],
                    "name_orchestra": ["Berliner Philharmoniker"],
                    "_links": {
                        "artist": [
                            {
                                "id": "1687",
                                "name": "Vítězslava Kaprálová",
                                "display_type": "person",
                                "fields_of_work": ["composer"],
                                "role": {"type": "composer", "name": "composer"},
                            }
                        ],
                        "epoch": [{"id": "4", "name": "Late Romanticism"}],
                    },
                },
                {
                    "id": "56434-2",
                    "title": "Fantasy for Violin and Orchestra in G minor, op. 24",
                    "is_encore": False,
                    "name_composers": ["Josef Suk"],
                    "name_conductor": ["Jakub Hrůša"],
                    "name_orchestra": ["Berliner Philharmoniker"],
                    "_links": {
                        "artist": [
                            {
                                "id": "1688",
                                "name": "Josef Suk",
                                "display_type": "person",
                                "fields_of_work": ["composer"],
                                "role": {"type": "composer", "name": "composer"},
                            },
                            {
                                "id": "1215",
                                "name": "Julia Fischer",
                                "display_type": "person",
                                "fields_of_work": ["soloist"],
                                "role": {"type": "instrument", "name": "violin"},
                            },
                        ],
                        "epoch": [{"id": "4", "name": "Late Romanticism"}],
                    },
                },
            ]
        },
    },
    {
        "id": "56465",
        "date": {"begin": 1759597200},  # 2025-10-04, Europe/Berlin
        "_links": {
            "season": [{"label": "2025/26"}],
            "artist": [
                {
                    "id": "1",
                    "name": "Berliner Philharmoniker",
                    "display_type": "group",
                    "group_name": "Berliner Philharmoniker",
                    "fields_of_work": [],
                    "role": {"type": "group", "name": "orchestra"},
                },
                {
                    "id": "123",
                    "name": "Daniel Barenboim",
                    "display_type": "person",
                    "fields_of_work": ["conductor", "pianist"],
                    "role": {"type": "conductor", "name": "conductor"},
                },
            ],
        },
        "_embedded": {
            "work": [
                {
                    "id": "56465-3",
                    "title": "Symphony No. 7 in A major, op. 92",
                    "is_encore": True,
                    "name_composers": ["Ludwig van Beethoven"],
                    "name_conductor": ["Daniel Barenboim"],
                    "name_orchestra": ["Berliner Philharmoniker"],
                    "_links": {
                        "artist": [
                            {
                                "id": "45",
                                "name": "Ludwig van Beethoven",
                                "display_type": "person",
                                "fields_of_work": ["composer"],
                                "role": {"type": "composer", "name": "composer"},
                            }
                        ],
                        "epoch": [{"id": "2", "name": "Classical"}],
                    },
                },
                # untitled entry (e.g. an applause segment) -> no record
                {"id": "56465-9", "title": "", "_links": {"artist": []}},
            ]
        },
    },
]


def performances() -> dict[str, SourceWorkMention]:
    mentions: dict[str, SourceWorkMention] = {}
    for concert in CONCERTS:
        for mention in _performances(concert):
            mentions[mention.external_id] = mention
    return mentions


def test_work_mention_carries_first_composer_title_and_context() -> None:
    mention = performances()["perf:56434-2"]
    assert mention.title == "Fantasy for Violin and Orchestra in G minor, op. 24"
    assert mention.composer == "Josef Suk"
    assert mention.raw["url"] == "https://www.digitalconcerthall.com/en/concert/56434"
    assert mention.raw["date"] == "2026-06-06"
    assert mention.raw["season"] == "2025/26"
    assert mention.raw["conductors"] == ["Jakub Hrůša"]
    # ensembles and periods are kept in raw (dropped from structured fields for now)
    assert mention.raw["ensembles"] == ["Berliner Philharmoniker"]
    assert mention.raw["periods"] == ["Late Romanticism"]


def test_soloist_role_name_becomes_discipline() -> None:
    mention = performances()["perf:56434-2"]
    assert mention.raw["soloists"] == [{"name": "Julia Fischer", "discipline": "violin"}]


def test_work_without_soloist_has_empty_soloists() -> None:
    mention = performances()["perf:56434-1"]
    assert mention.raw["soloists"] == []
    assert mention.composer == "Vítězslava Kaprálová"
    assert mention.raw["is_encore"] is False


def test_encore_flag_and_local_date_are_recorded() -> None:
    mention = performances()["perf:56465-3"]
    assert mention.raw["is_encore"] is True
    assert mention.raw["date"] == "2025-10-04"
    assert mention.raw["periods"] == ["Classical"]
    assert mention.composer == "Ludwig van Beethoven"


def test_untitled_work_yields_no_mention() -> None:
    assert "perf:56465-9" not in performances()


def artists() -> dict[str, SourceRecord]:
    registry: dict[str, _Artist] = {}
    for concert in CONCERTS:
        _collect(concert, registry)
    # Mirrors how the adapter builds these: _artist_record over the registry.
    records = (_artist_record(info) for info in registry.values())
    return {record.external_id: record for record in records}


def test_orchestra_becomes_a_claimless_ensemble_record() -> None:
    record = artists()["artist:1"]
    assert record.kind == "ensemble"
    assert record.name == "Berliner Philharmoniker"
    assert record.url == "https://www.digitalconcerthall.com/en/artist/1"
    assert record.claims == ()


def test_soloist_records_instrument_as_performs_as() -> None:
    record = artists()["artist:1215"]
    assert record.kind == "person"
    assert record.name == "Julia Fischer"
    assert record.claims == (
        SourceClaim("has_profession", "profession", "soloist"),
        SourceClaim("performs_as", value="violin"),
    )


def test_professions_union_fields_of_work_and_role() -> None:
    # Barenboim's fields_of_work list both professions; sorted, no duplicates
    record = artists()["artist:123"]
    assert record.claims == (
        SourceClaim("has_profession", "profession", "conductor"),
        SourceClaim("has_profession", "profession", "pianist"),
    )


def test_same_artist_across_concerts_is_one_record() -> None:
    parsed = artists()
    ids = list(parsed)
    assert ids.count("artist:1") == 1  # the orchestra appears in both concerts
    assert set(parsed) == {
        "artist:1",
        "artist:1562",
        "artist:1687",
        "artist:1688",
        "artist:1215",
        "artist:123",
        "artist:45",
    }


def test_register_skips_artists_without_id_or_name() -> None:
    registry: dict[str, _Artist] = {}
    _register(registry, {"display_type": "person", "role": {"type": "composer"}})  # no id/name
    _register(registry, {"id": "9", "display_type": "group", "group_name": None})  # group, no name
    assert registry == {}
