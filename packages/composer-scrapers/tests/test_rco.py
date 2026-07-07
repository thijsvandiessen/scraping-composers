"""Tests for RCO parsing: conductor extraction and concert work mentions."""

from typing import Any

from composer_schema import SourceClaim
from composer_scrapers.rco.artists import (
    _Credit,
    collect_credits,
    credit_record,
    iter_conductor_records,
)
from composer_scrapers.rco.performances import _performances

# Minimal conductors page JSON with two conductors in one person_group block
CONDUCTORS_PAGE = {
    "content": [
        {"type": "intro", "text": "<p>The orchestra's conductors.</p>"},
        {
            "type": "person_group",
            "title": "Chief conductors",
            "intro": "",
            "persons": [
                {
                    "id": 346,
                    "meta": {"type": "people.PersonPage", "slug": "daniele-gatti"},
                    "title": "Daniele Gatti",
                    "url": "/en/orchestra/conductors/daniele-gatti/",
                    "person": {
                        "meta": {"id": 600, "referenceId": "a162o00000DDLjUAAX"},
                        "name": "Daniele Gatti",
                        "introduction": "",
                        "description": "<p>Born in Milan, Daniele Gatti...</p>",
                        "role": {"label": "conductor"},
                        "function": {"label": "chief conductor 2016-2018"},
                        "defaultAsset": {
                            "meta": {"id": 202, "referenceId": "abc123"},
                            "url": "https://cdn.example.com/abc123_original.jpg",
                            "renditions": {"600x600": "https://cdn.example.com/abc123_600x600.jpg"},
                        },
                    },
                },
                {
                    "id": 347,
                    "meta": {"type": "people.PersonPage", "slug": "han-na-chang"},
                    "title": "Han-Na Chang",
                    "url": "/en/orchestra/conductors/han-na-chang/",
                    "person": {
                        "meta": {"id": 601, "referenceId": "b162o00000DDLjUAAX"},
                        "name": "Han-Na Chang",
                        "introduction": "",
                        "description": "",
                        "role": {"label": "conductor"},
                        "function": None,
                        "defaultAsset": None,
                    },
                },
            ],
        },
    ]
}

# Minimal concert detail JSON with two works and one interval
CONCERT = {
    "meta": {"id": 2175, "slug": "vikingur-olafsson-beethoven-2026-08-26"},
    "title": "Víkingur Ólafsson and Beethoven's Emperor Concerto",
    "start": "2026-08-26T20:00:00+02:00",
    "location": "Concertgebouw, Amsterdam",
    "websiteUrls": {
        "en": "https://www.concertgebouworkest.nl/en/calendar/vikingur-olafsson-beethoven-2026-08-26/",
    },
    "program": [
        {
            "nameEn": "Piano Concerto No. 5, 'Emperor'",
            "relatedCredit": "Ludwig van Beethoven",
            "durationMinutes": 40,
            "instrumentation": "2.2.2.2 - 2.2.0.0 - pk - str",
            "isBreak": False,
        },
        {"nameEn": "-- interval --", "relatedCredit": "-- interval", "durationMinutes": 25, "isBreak": False},
        {
            "nameEn": "Symphony No. 5",
            "relatedCredit": "Sergei Prokofiev",
            "durationMinutes": 43,
            "instrumentation": "3.3.4.3 - 4.3.3.1 - pk - str",
            "isBreak": False,
        },
    ],
    "credits": [
        {
            "name": "Santtu-Matias Rouvali",
            "roleEn": "conductor",
            "imageRenditions": {"600x600": "https://cdn.example.com/r_600x600.jpg"},
            "url": "/en/orchestra/conductors/santtu-matias-rouvali/",
        },
        {
            "name": "Víkingur Ólafsson",
            "roleEn": "piano",
            "imageRenditions": {"600x600": "https://cdn.example.com/v_600x600.jpg"},
            "url": None,
        },
    ],
}


# ---------------------------------------------------------------------------
# Conductor records from the conductors overview page
# ---------------------------------------------------------------------------


def test_iter_conductor_records_extracts_all_conductors() -> None:
    records = iter_conductor_records(CONDUCTORS_PAGE)
    assert len(records) == 2


def test_conductor_record_fields() -> None:
    record = iter_conductor_records(CONDUCTORS_PAGE)[0]
    assert record.external_id == "credit:600"
    assert record.name == "Daniele Gatti"
    assert record.url == "https://www.concertgebouworkest.nl/en/orchestra/conductors/daniele-gatti/"
    assert record.kind == "person"
    assert record.raw["reference_id"] == "a162o00000DDLjUAAX"
    assert record.raw["function"] == "chief conductor 2016-2018"
    assert record.raw["image_url"] == "https://cdn.example.com/abc123_600x600.jpg"


def test_conductor_record_claims_include_profession_and_function() -> None:
    record = iter_conductor_records(CONDUCTORS_PAGE)[0]
    assert SourceClaim("has_profession", "profession", "conductor") in record.claims
    assert SourceClaim("has_function", value="chief conductor 2016-2018") in record.claims


def test_conductor_without_function_has_no_function_claim() -> None:
    record = iter_conductor_records(CONDUCTORS_PAGE)[1]
    assert record.name == "Han-Na Chang"
    assert all(c.predicate != "has_function" for c in record.claims)


def test_conductor_without_portrait_has_null_image() -> None:
    record = iter_conductor_records(CONDUCTORS_PAGE)[1]
    assert record.raw["image_url"] is None


def test_iter_conductor_records_skips_non_person_group_blocks() -> None:
    page: dict[str, Any] = {"content": [{"type": "intro", "text": "<p>...</p>"}]}
    assert iter_conductor_records(page) == []


# ---------------------------------------------------------------------------
# Work mentions from a concert detail
# ---------------------------------------------------------------------------


def test_performances_skips_interval() -> None:
    mentions = _performances(CONCERT)
    assert len(mentions) == 2
    assert all("interval" not in m.title for m in mentions)


def test_performances_external_id_uses_concert_id_and_programme_index() -> None:
    mentions = _performances(CONCERT)
    assert mentions[0].external_id == "perf:2175:0"
    assert mentions[1].external_id == "perf:2175:2"  # index 1 was the interval


def test_performances_composer_and_title() -> None:
    mention = _performances(CONCERT)[0]
    assert mention.composer == "Ludwig van Beethoven"
    assert mention.title == "Piano Concerto No. 5, 'Emperor'"


def test_performances_raw_contains_concert_context() -> None:
    mention = _performances(CONCERT)[0]
    assert mention.raw["date"] == "2026-08-26T20:00:00+02:00"
    assert mention.raw["venue"] == "Concertgebouw, Amsterdam"
    assert mention.raw["conductor"] == "Santtu-Matias Rouvali"
    assert mention.raw["soloists"] == [{"name": "Víkingur Ólafsson", "discipline": "piano"}]
    assert mention.raw["instrumentation"] == "2.2.2.2 - 2.2.0.0 - pk - str"
    assert mention.raw["duration_minutes"] == 40
    assert mention.raw["programme_idx"] == 0


def test_performances_url_is_concert_page() -> None:
    mention = _performances(CONCERT)[0]
    assert mention.raw["url"] == (
        "https://www.concertgebouworkest.nl/en/calendar/vikingur-olafsson-beethoven-2026-08-26/"
    )


def test_performances_empty_programme_returns_empty() -> None:
    assert _performances({**CONCERT, "program": []}) == []


def test_performances_all_intervals_returns_empty() -> None:
    concert = {
        **CONCERT,
        "program": [{"nameEn": "-- interval --", "relatedCredit": "-- interval", "durationMinutes": 20}],
    }
    assert _performances(concert) == []


# ---------------------------------------------------------------------------
# Credit accumulation from concert credits
# ---------------------------------------------------------------------------


def test_collect_credits_registers_conductor_and_soloist() -> None:
    registry: dict[str, _Credit] = {}
    collect_credits(CONCERT, registry)
    assert "conductor:Santtu-Matias Rouvali" in registry
    assert "piano:Víkingur Ólafsson" in registry


def test_collect_credits_accumulates_concert_ids_across_concerts() -> None:
    registry: dict[str, _Credit] = {}
    collect_credits(CONCERT, registry)
    concert2 = {**CONCERT, "meta": {"id": 9999, "slug": "other-concert"}}
    collect_credits(concert2, registry)
    assert registry["conductor:Santtu-Matias Rouvali"].concert_ids == {2175, 9999}


def test_credit_record_conductor_has_no_performs_as() -> None:
    credit = _Credit(name="Santtu-Matias Rouvali", role_en="conductor", image_url=None)
    credit.concert_ids.add(2175)
    record = credit_record(credit)
    assert record.external_id == "credit:conductor:Santtu-Matias Rouvali"
    assert SourceClaim("has_profession", "profession", "conductor") in record.claims
    assert all(c.predicate != "performs_as" for c in record.claims)


def test_credit_record_soloist_has_performs_as_discipline() -> None:
    credit = _Credit(name="Víkingur Ólafsson", role_en="piano", image_url=None)
    record = credit_record(credit)
    assert SourceClaim("has_profession", "profession", "soloist") in record.claims
    assert SourceClaim("performs_as", value="piano") in record.claims
