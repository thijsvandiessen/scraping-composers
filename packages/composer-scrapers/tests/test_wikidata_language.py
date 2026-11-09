"""The Wikidata adapters keep the language tag of the label they name a document by.

The label service answers in ``en`` or ``mul`` (a name spelled the same in every
language), and a bare QID, untagged, when the item has neither; the ensembles
adapter falls back to a label in a language of ``NAME_LANGUAGES``. Each document
carries the tag of the label it actually used, and none when the label had none.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from composer_schema import EntityDocument, WorkMentionDocument
from composer_scrapers.wikidata.catalogue import _mentions_from_rows as catalogue_mentions
from composer_scrapers.wikidata.ensembles import _documents_from_rows as ensemble_documents
from composer_scrapers.wikidata.instrumentation import documents_from_rows as instrumentation_documents
from composer_scrapers.wikidata.parse import _language, _records_from_rows
from composer_scrapers.wikidata.places import _fold_place_rows, _place_document
from composer_scrapers.wikidata.works import _mentions_from_rows as work_mentions

NOW = datetime(2026, 1, 1, tzinfo=UTC)
ENTITY = "http://www.wikidata.org/entity/"


def uri(qid: str) -> dict[str, str]:
    return {"type": "uri", "value": f"{ENTITY}{qid}"}


def lit(value: str, lang: str | None = None) -> dict[str, str]:
    binding = {"type": "literal", "value": value}
    if lang is not None:
        binding["xml:lang"] = lang
    return binding


def labelled(qid: str, label: str, lang: str | None, **bindings: dict[str, str]) -> dict[str, Any]:
    return {"item": uri(qid), "itemLabel": lit(label, lang), **bindings}


COMPOSER = {"composer": uri("Q255"), "composerLabel": lit("Ludwig van Beethoven", "en")}


def test_a_label_gives_its_language_tag() -> None:
    assert _language({"itemLabel": lit("Mozart", "mul")}, "itemLabel") == "mul"
    assert _language({"itemLabel": lit("Q254")}, "itemLabel") is None  # an echoed QID
    assert _language({"item": uri("Q254")}, "item") is None  # an item is no text
    assert _language({}, "itemLabel") is None


def test_a_person_carries_its_labels_language() -> None:
    (english,) = _records_from_rows([labelled("Q255", "Ludwig van Beethoven", "en")])
    (neutral,) = _records_from_rows([labelled("Q254", "Wolfgang Amadeus Mozart", "mul")])
    assert (english.language, neutral.language) == ("en", "mul")


def test_a_work_mention_carries_its_titles_language() -> None:
    row = labelled(
        "Q151",
        "Symphony No. 9",
        "en",
        **COMPOSER,
        premiere=lit("1824-05-07T00:00:00Z"),
        premierePrecision=lit("11"),
    )
    (mention,) = work_mentions([row], NOW)
    assert mention.language == "en"


def test_a_catalogue_mention_carries_its_titles_language() -> None:
    (mention,) = catalogue_mentions([labelled("Q1", "Requiem", "mul", **COMPOSER)], NOW)
    assert mention.language == "mul"


def test_a_place_carries_the_language_of_the_label_it_kept() -> None:
    rows = [
        {"item": uri("Q586"), "coord": lit("Point(7.1 50.73)")},  # a row without the label
        labelled("Q586", "Bonn", "en"),
    ]
    (qid, place), *_ = _fold_place_rows(rows).items()
    assert _place_document(qid, place, NOW).language == "en"


def test_an_instrumentation_work_carries_its_titles_language() -> None:
    row = labelled(
        "Q2",
        "Symphony No. 2",
        "en",
        **COMPOSER,
        statement=uri("statement/Q2-s1"),
        instrument=uri("Q5994"),
        instrumentLabel=lit("piano", "en"),
    )
    documents = instrumentation_documents([row], NOW)
    assert {type(d) for d in documents} == {WorkMentionDocument, EntityDocument}
    assert {d.language for d in documents} == {"en"}


def test_an_ensemble_named_by_the_label_service_carries_that_labels_language() -> None:
    (document,) = ensemble_documents([labelled("Q1", "Berlin Philharmonic", "en")], {"Q1"}, NOW)
    assert document.language == "en"


def test_an_ensemble_named_from_another_language_carries_that_language() -> None:
    # no en/mul label (#363): the name comes from NAME_LANGUAGES, de before fr
    rows = [
        labelled("Q1", "Q1", None),
        {"item": uri("Q1"), "name": lit("Orchestre de Budapest", "fr"), "nameLang": lit("fr")},
        {"item": uri("Q1"), "name": lit("Budapester Sinfonieorchester", "de"), "nameLang": lit("de")},
    ]
    (document,) = ensemble_documents(rows, {"Q1"}, NOW)
    assert (document.name, document.language) == ("Budapester Sinfonieorchester", "de")
