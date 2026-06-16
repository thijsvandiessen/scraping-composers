"""Tests for aggregating the NY Phil performance-history JSON into records."""

from composer_ingest.sources import SourceClaim, SourceRecord
from composer_ingest.sources.nyphil.people import ROLES, _aggregate, _record
from composer_ingest.sources.nyphil.performances import _performances

# Trimmed copy of the real structure: double-spaced names, ";"-joined
# conductors, the "Not conducted" sentinel, soloists with and without an
# instrument, and a person recurring across programs and roles.
PROGRAMS = [
    {
        "season": "1842-43",
        "programID": "3853",
        "concerts": [
            {
                "Date": "1842-12-07T05:00:00Z",
                "eventType": "Subscription Season",
                "Venue": "Apollo Rooms",
                "Location": "Manhattan, NY",
            },
        ],
        "works": [
            {
                "ID": "52446*",
                "workTitle": "SYMPHONY NO. 5",
                "composerName": "Beethoven,  Ludwig  van",
                "conductorName": "Hill, Ureli Corelli",
                "soloists": [],
            },
            {
                "ID": "8834*4",
                "workTitle": "OBERON",
                "composerName": "Weber,  Carl  Maria Von",
                "conductorName": "Rudel, Julius;  Not conducted",
                "soloists": [
                    {"soloistName": "Otto, Antoinette", "soloistRoles": "S", "soloistInstrument": "Soprano"},
                    {"soloistName": " ", "soloistRoles": "S", "soloistInstrument": "Piano"},
                ],
            },
            {"ID": "0*", "interval": "Intermission", "soloists": []},
        ],
    },
    {
        "season": "1844-45",
        "programID": "5178",
        "concerts": [
            {"Date": "1844-11-16T05:00:00Z", "Venue": "Apollo Rooms", "Location": "Manhattan, NY"},
            {"Date": "1844-11-23T05:00:00Z", "Venue": "Apollo Rooms", "Location": "Manhattan, NY"},
        ],
        "works": [
            {
                "ID": "52446*2",
                "workTitle": "SYMPHONY NO. 5",
                "composerName": "Beethoven, Ludwig van",
                "conductorName": "Timm, Henry C.",
                "soloists": [
                    {
                        "soloistName": "Otto, Antoinette",
                        "soloistRoles": "S",
                        "soloistInstrument": "Mezzo-Soprano",
                    },
                    {"soloistName": "Timm, Henry C.", "soloistRoles": "A", "soloistInstrument": ""},
                ],
            },
            {
                "ID": "52446*3",
                "workTitle": "EGMONT",
                "composerName": "Beethoven, Ludwig van",
                "conductorName": "Timm, Henry C.",
                "soloists": [],
            },
            {
                "ID": "9001*",
                "workTitle": {"em": "PRINCE IGOR", "_": "CHORUS FROM  (ARR.)"},
                "composerName": "Borodin, Alexander",
                "conductorName": "Timm, Henry C.",
                "soloists": [],
            },
        ],
    },
]


def records() -> dict[str, SourceRecord]:
    people = _aggregate(PROGRAMS)
    return {f"{role}:{name}": _record(role, name, person) for (role, name), person in people.items()}


def test_whitespace_runs_collapse_and_programs_count_once() -> None:
    # "Beethoven,  Ludwig  van" and "Beethoven, Ludwig van" are one record;
    # two works on program 5178 still count it as one program
    record = records()["composer:Beethoven, Ludwig van"]
    assert record.external_id == "composer:Beethoven, Ludwig van"
    assert record.url is None
    assert record.raw == {
        "role": "composer",
        "name": "Beethoven, Ludwig van",
        "program_count": 2,
        "first_season": "1842-43",
        "last_season": "1844-45",
    }
    assert record.claims == (
        SourceClaim("has_profession", "profession", "composer"),
        SourceClaim("program_count", value="2"),
        SourceClaim("first_season", value="1842-43"),
        SourceClaim("last_season", value="1844-45"),
    )


def test_joined_conductors_split_and_sentinel_dropped() -> None:
    parsed = records()
    assert "conductor:Rudel, Julius" in parsed
    assert not any("Not conducted" in key for key in parsed)


def test_soloist_instruments_become_claims() -> None:
    record = records()["soloist:Otto, Antoinette"]
    assert record.claims == (
        SourceClaim("has_profession", "profession", "soloist"),
        SourceClaim("performs_as", "discipline", "Mezzo-Soprano"),
        SourceClaim("performs_as", "discipline", "Soprano"),
        SourceClaim("program_count", value="2"),
        SourceClaim("first_season", value="1842-43"),
        SourceClaim("last_season", value="1844-45"),
    )
    assert record.raw["instruments"] == ["Mezzo-Soprano", "Soprano"]


def test_empty_instrument_yields_no_performs_as_claim() -> None:
    record = records()["soloist:Timm, Henry C."]
    assert not any(claim.predicate == "performs_as" for claim in record.claims)


def test_same_person_gets_one_record_per_role() -> None:
    parsed = records()
    assert "conductor:Timm, Henry C." in parsed
    assert "soloist:Timm, Henry C." in parsed


def test_blank_soloist_names_and_intervals_are_skipped() -> None:
    parsed = records()
    roles = {key.split(":", 1)[0] for key in parsed}
    assert roles <= set(ROLES)
    assert "soloist:" not in parsed
    expected = {
        "composer:Beethoven, Ludwig van",
        "composer:Borodin, Alexander",
        "composer:Weber, Carl Maria Von",
        "conductor:Hill, Ureli Corelli",
        "conductor:Rudel, Julius",
        "conductor:Timm, Henry C.",
        "soloist:Otto, Antoinette",
        "soloist:Timm, Henry C.",
    }
    assert set(parsed) == expected


def performances() -> dict[str, SourceRecord]:
    return {record.external_id: record for record in _performances(PROGRAMS)}


def test_performance_record_links_work_to_people_and_concert() -> None:
    record = performances()["perf:3853:0:0"]
    assert record.kind == "work"
    assert record.name == "SYMPHONY NO. 5"
    assert record.url is None
    assert record.raw["date"] == "1842-12-07"
    assert record.raw["venue"] == "Apollo Rooms"
    # no soloists on this work -> no performed_by claim; names are collapsed
    assert record.claims == (
        SourceClaim("composed_by", "person", "Beethoven, Ludwig van"),
        SourceClaim("conducted_by", "person", "Hill, Ureli Corelli"),
        SourceClaim("performed_on", value="1842-12-07"),
        SourceClaim("performed_in", "place", "Manhattan, NY"),
    )


def test_interval_entries_skipped_but_work_index_preserved() -> None:
    parsed = performances()
    # OBERON is the second work (index 1); the interval (index 2) yields nothing
    assert parsed["perf:3853:0:1"].name == "OBERON"
    assert "perf:3853:0:2" not in parsed


def test_multi_concert_program_yields_one_record_per_concert() -> None:
    parsed = performances()
    first, second = parsed["perf:5178:0:0"], parsed["perf:5178:1:0"]
    assert first.name == second.name == "SYMPHONY NO. 5"
    assert first.raw["date"] == "1844-11-16"
    assert second.raw["date"] == "1844-11-23"


def test_performance_drops_not_conducted_and_keeps_named_soloists() -> None:
    record = performances()["perf:3853:0:1"]  # OBERON
    conductors = [c.object_label for c in record.claims if c.predicate == "conducted_by"]
    soloists = [c.object_label for c in record.claims if c.predicate == "performed_by"]
    assert conductors == ["Rudel, Julius"]  # "Not conducted" sentinel dropped
    assert soloists == ["Otto, Antoinette"]  # blank soloist name dropped


def test_dict_work_title_is_joined_into_one_string() -> None:
    record = performances()["perf:5178:0:2"]  # third work of the first concert
    assert record.name == "PRINCE IGOR CHORUS FROM (ARR.)"
    assert SourceClaim("composed_by", "person", "Borodin, Alexander") in record.claims
