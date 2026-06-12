"""Tests for aggregating the NY Phil performance-history JSON into records."""

from composer_ingest.sources import SourceClaim, SourceRecord
from composer_ingest.sources.nyphil import ROLES, _aggregate, _record

# Trimmed copy of the real structure: double-spaced names, ";"-joined
# conductors, the "Not conducted" sentinel, soloists with and without an
# instrument, and a person recurring across programs and roles.
PROGRAMS = [
    {
        "season": "1842-43",
        "programID": "3853",
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
        "composer:Weber, Carl Maria Von",
        "conductor:Hill, Ureli Corelli",
        "conductor:Rudel, Julius",
        "conductor:Timm, Henry C.",
        "soloist:Otto, Antoinette",
        "soloist:Timm, Henry C.",
    }
    assert set(parsed) == expected
