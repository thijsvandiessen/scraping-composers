"""Tests for the Wikidata instrumentation adapter: works with a P870 statement."""

import re
import urllib.parse
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from composer_http.testing import mock_session
from composer_schema import EntityDocument, SourceClaim, WorkMentionDocument
from composer_scrapers import REGISTRY
from composer_scrapers.wikidata import WikidataInstrumentationAdapter
from composer_scrapers.wikidata.instrumentation import (
    INSTRUMENTATION_ID_QUERY,
    INSTRUMENTATION_QUERY,
    Part,
    _fetch_instrumentation_page,
    _fetch_instrumentation_qids,
    documents_from_rows,
    scoring,
)

NOW = datetime(2026, 11, 1, tzinfo=UTC)
ENTITY = "http://www.wikidata.org/entity/"
BRAHMS = "Q7294=Johannes Brahms"


def uri(value: str) -> dict[str, str]:
    return {"type": "uri", "value": value if value.startswith("http") else f"{ENTITY}{value}"}


def lit(value: str) -> dict[str, str]:
    return {"type": "literal", "value": value}


def row(
    qid: str,
    instrument: str | None,
    statement: str = "s1",
    label: str | None = "Symphony No. 2",
    **qualifiers: str | None,
) -> dict[str, Any]:
    """An INSTRUMENTATION_QUERY row; ``instrument`` and the ``role`` qualifier are
    ``QID=label``, an instrument without ``=`` is an "unknown value" blank node.
    ``composer`` (``QID=label``, Brahms by default) and ``quantity`` are the other
    keywords."""
    composer = qualifiers.get("composer") or BRAHMS
    quantity, role = qualifiers.get("quantity"), qualifiers.get("role")
    composer_qid, composer_label = composer.split("=")
    bindings: dict[str, Any] = {
        "item": uri(qid),
        "composer": uri(composer_qid),
        "composerLabel": lit(composer_label),
        "statement": uri(f"{ENTITY}statement/{qid}-{statement}"),
    }
    if label is not None:
        bindings["itemLabel"] = lit(label)
    if instrument is not None and "=" in instrument:
        instrument_qid, instrument_label = instrument.split("=")
        bindings["instrument"] = uri(instrument_qid)
        bindings["instrumentLabel"] = lit(instrument_label)
    elif instrument is not None:
        bindings["instrument"] = {"type": "bnode", "value": instrument}
    if quantity is not None:
        bindings["quantity"] = lit(quantity)
    if role is not None:
        role_qid, role_label = role.split("=")
        bindings["role"] = uri(role_qid)
        bindings["roleLabel"] = lit(role_label)
    return bindings


def _mention(documents: list[WorkMentionDocument | EntityDocument]) -> WorkMentionDocument:
    (mention,) = [d for d in documents if isinstance(d, WorkMentionDocument)]
    return mention


def _entity(documents: list[WorkMentionDocument | EntityDocument]) -> EntityDocument:
    (entity,) = [d for d in documents if isinstance(d, EntityDocument)]
    return entity


def _edges(entity: EntityDocument, predicate: str) -> list[str]:
    return [c.object_label for c in entity.claims if c.predicate == predicate and c.object_label]


#: Brahms's Second as a part-by-part list, the way Wikidata states a detailed
#: instrumentation: one statement per instrument, P1114 for the count.
BRAHMS_2 = [
    ("Q5994=flute", "2"),
    ("Q8377=oboe", "2"),
    ("Q8343=clarinet", "2"),
    ("Q123=bassoon", "2"),
    ("Q193=horn", "4"),
    ("Q8338=trumpet", "2"),
    ("Q8350=trombone", "3"),
    ("Q202=tuba", None),
    ("Q6=timpani", None),
    ("Q7=string section", None),
]


def brahms_rows() -> list[dict[str, Any]]:
    return [
        row("Q1", instrument, f"s{n}", quantity=quantity) for n, (instrument, quantity) in enumerate(BRAHMS_2)
    ]


def test_a_symphony_lists_its_parts_with_counts() -> None:
    documents = documents_from_rows(brahms_rows(), NOW)
    mention, entity = _mention(documents), _entity(documents)

    assert (mention.id, mention.title, mention.composer) == ("Q1:Q7294", "Symphony No. 2", "Johannes Brahms")
    assert mention.source_name == "wikidata_instrumentation"
    assert mention.url == "https://www.wikidata.org/wiki/Q1"
    assert mention.raw["item"] == f"{ENTITY}Q1"
    assert mention.raw["composer_item"] == f"{ENTITY}Q7294"

    parts = {part["label"]: part for part in mention.raw["instrumentation"]["parts"]}
    assert parts["horn"] == {"item": "Q193", "label": "horn", "instrument": "horn", "count": 4}
    assert parts["string section"]["instrument"] == "strings"
    assert "count" not in parts["tuba"]
    assert mention.raw["instrumentation"]["unmatched"] == []

    assert entity.kind == "work"
    assert entity.name == "Symphony No. 2 (Johannes Brahms)"
    assert entity.url == "https://www.wikidata.org/wiki/Q1"
    assert entity.raw == {k: v for k, v in mention.raw.items() if k != "composer_item"}
    # a list of an orchestra's parts: what it includes, not what it is for
    assert _edges(entity, "written_for") == []
    assert set(_edges(entity, "includes_instrument")) == {
        "flute", "oboe", "clarinet", "bassoon", "horn", "trumpet", "trombone", "tuba", "timpani", "strings",
    }  # fmt: skip


def test_the_composer_edge_is_pinned_to_its_item() -> None:
    entity = _entity(documents_from_rows(brahms_rows(), NOW))
    (composed_by,) = [c for c in entity.claims if c.predicate == "composed_by"]
    assert composed_by == SourceClaim("composed_by", "person", "Johannes Brahms", object_wikidata_id="Q7294")


def test_a_concerto_is_for_its_soloist_and_the_orchestra() -> None:
    rows = [
        row("Q2", "Q8355=violin", "s1", "Violin Concerto"),
        row("Q2", "Q42998=orchestra", "s2", "Violin Concerto"),
    ]
    entity = _entity(documents_from_rows(rows, NOW))
    assert _edges(entity, "written_for") == ["orchestra", "violin"]
    assert _edges(entity, "includes_instrument") == ["violin"]


def test_a_soloist_role_is_for_the_soloist_even_in_a_parts_list() -> None:
    rows = [
        row("Q3", "Q5994=flute", "s1", "Concerto", quantity="2"),
        row("Q3", "Q5994=flute", "s2", "Concerto", role="Q9=soloist"),
        row("Q3", "Q7=string section", "s3", "Concerto"),
    ]
    documents = documents_from_rows(rows, NOW)
    parts = _mention(documents).raw["instrumentation"]["parts"]
    # two statements naming one instrument stay two parts
    assert [(p["label"], p.get("count"), p.get("solo")) for p in parts] == [
        ("flute", 2, None),
        ("flute", None, True),
        ("string section", None, None),
    ]
    assert parts[1]["roles"] == ["soloist"]
    assert _edges(_entity(documents), "written_for") == ["flute"]


def test_a_short_list_of_instruments_is_what_the_work_is_for() -> None:
    rows = [row("Q4", "Q8355=violin", "s1", "Violin Sonata"), row("Q4", "Q12=piano", "s2", "Violin Sonata")]
    assert _edges(_entity(documents_from_rows(rows, NOW)), "written_for") == ["piano", "violin"]


def test_an_ensemble_is_for_itself_and_includes_its_members() -> None:
    rows = [row("Q5", "Q207338=string quartet", label="String Quartet No. 1")]
    entity = _entity(documents_from_rows(rows, NOW))
    assert _edges(entity, "written_for") == ["string quartet"]
    assert _edges(entity, "includes_instrument") == ["violin", "viola", "cello"]


def test_a_combination_category_expands_to_what_it_names() -> None:
    rows = [row("Q6", "Q11=piano four hands", label="Hungarian Dances")]
    assert _edges(_entity(documents_from_rows(rows, NOW)), "written_for") == ["piano four hands", "piano"]


@pytest.mark.parametrize(
    "parts",
    [
        [Part("Q1", "flute", count=1), Part("Q2", "piano")],
        [Part("Q1", "flute"), Part("Q2", "string section")],
        [Part(f"Q{n}", name) for n, name in enumerate(["flute", "oboe", "clarinet", "bassoon", "horn"])],
        # one more than MAX_FORCES
    ],
    ids=["a count", "a section", "too many instruments"],
)
def test_a_parts_list_is_only_included(parts: list[Part]) -> None:
    read = scoring(parts)
    assert read.written_for == ()
    assert "flute" in read.includes


def test_an_unknown_label_is_kept_but_claims_nothing() -> None:
    rows = [row("Q7", "Q8355=violin", "s1", "Sonata"), row("Q7", "Q99=nyckelharpa", "s2", "Sonata")]
    documents = documents_from_rows(rows, NOW)
    mention = _mention(documents)
    assert mention.raw["instrumentation"]["unmatched"] == ["nyckelharpa"]
    nyckelharpa = next(p for p in mention.raw["instrumentation"]["parts"] if p["item"] == "Q99")
    assert nyckelharpa["instrument"] is None
    assert _edges(_entity(documents), "written_for") == ["violin"]


@pytest.mark.parametrize("quantity", ["2", "+2", "2.0"])
def test_a_quantity_is_read_as_a_whole_number(quantity: str) -> None:
    rows = [row("Q8", "Q193=horn", quantity=quantity)]
    (part,) = _mention(documents_from_rows(rows, NOW)).raw["instrumentation"]["parts"]
    assert part["count"] == 2


@pytest.mark.parametrize("quantity", ["0", "1.5", "two"])
def test_a_quantity_that_is_no_count_is_dropped(quantity: str) -> None:
    rows = [row("Q8", "Q193=horn", quantity=quantity)]
    (part,) = _mention(documents_from_rows(rows, NOW)).raw["instrumentation"]["parts"]
    assert "count" not in part


def test_a_collaboration_gets_one_mention_per_composer_and_one_entity() -> None:
    rows = [
        row("Q9", "Q8355=violin", label="F-A-E Sonata", composer="Q7351=Robert Schumann"),
        row("Q9", "Q8355=violin", label="F-A-E Sonata", composer=BRAHMS),
    ]
    documents = documents_from_rows(rows, NOW)
    mentions = [d for d in documents if isinstance(d, WorkMentionDocument)]
    assert [(m.id, m.composer) for m in mentions] == [
        ("Q9:Q7294", "Johannes Brahms"),
        ("Q9:Q7351", "Robert Schumann"),
    ]
    assert _entity(documents).name == "F-A-E Sonata (Johannes Brahms / Robert Schumann)"


def test_unnameable_works_and_unknown_instruments_yield_nothing() -> None:
    rows = [
        row("Q1", "Q8355=violin", label="Q1"),
        row("Q2", "Q8355=violin", label=None),
        row("Q3", "Q8355=violin", composer="Q9=Q9"),
        row("Q4", "_:b0"),
    ]
    assert documents_from_rows(rows, NOW) == []


def test_queries_ask_for_the_instrumentation_properties() -> None:
    assert "wdt:P86" in INSTRUMENTATION_ID_QUERY and "p:P870" in INSTRUMENTATION_ID_QUERY
    for prop in ("P86", "P870", "P1114", "P3831"):
        assert prop in INSTRUMENTATION_QUERY
    # both leave deprecated statements out, so the coverage check holds
    for query in (INSTRUMENTATION_ID_QUERY, INSTRUMENTATION_QUERY):
        assert "wikibase:DeprecatedRank" in query


def test_fetch_page_requires_every_work() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = urllib.parse.parse_qs(request.read().decode())["query"][0]
        assert "VALUES ?item { wd:Q1 wd:Q2 }" in query
        return httpx.Response(200, json={"results": {"bindings": [row("Q1", "Q8355=violin")]}})

    with mock_session(handler) as session:
        with pytest.raises(RuntimeError, match="1 of 2 requested works"):
            _fetch_instrumentation_page(session, ["Q1", "Q2"])


def test_fetch_qids_orders_numerically() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert urllib.parse.parse_qs(request.read().decode())["query"][0] == INSTRUMENTATION_ID_QUERY
        bindings = [{"item": uri(q)} for q in ("Q100", "Q20", "Q3", "Q20")]
        return httpx.Response(200, json={"results": {"bindings": bindings}})

    with mock_session(handler) as session:
        assert _fetch_instrumentation_qids(session) == ["Q3", "Q20", "Q100"]


def _fake_wdqs(monkeypatch: pytest.MonkeyPatch, population: list[str]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = urllib.parse.parse_qs(request.read().decode())["query"][0]
        if "VALUES" not in query:
            return httpx.Response(200, json={"results": {"bindings": [{"item": uri(q)} for q in population]}})
        qids = re.findall(r"wd:(Q\d+)", query.split("VALUES", 1)[1].split("}", 1)[0])
        rows = [row(qid, "Q8355=violin", label=f"Work {qid}") for qid in qids]
        return httpx.Response(200, json={"results": {"bindings": rows}})

    monkeypatch.setattr(WikidataInstrumentationAdapter, "open_session", lambda self: mock_session(handler))


def test_fetch_pages_through_the_population(monkeypatch: pytest.MonkeyPatch) -> None:
    population = [f"Q{n}" for n in range(1, 1201)]
    _fake_wdqs(monkeypatch, population)

    fetched = [d.id for d in WikidataInstrumentationAdapter().fetch() if isinstance(d, WorkMentionDocument)]
    assert fetched == [f"{qid}:Q7294" for qid in population]


def test_max_pages_caps_the_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_wdqs(monkeypatch, [f"Q{n}" for n in range(1, 1201)])
    entities = [
        d for d in WikidataInstrumentationAdapter().fetch(max_pages=1) if isinstance(d, EntityDocument)
    ]
    assert len(entities) == 500


def test_fetch_refuses_an_empty_population(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_wdqs(monkeypatch, [])
    with pytest.raises(RuntimeError, match="no works with an instrumentation"):
        list(WikidataInstrumentationAdapter().fetch())


def test_the_adapter_is_registered() -> None:
    assert isinstance(REGISTRY["wikidata_instrumentation"], WikidataInstrumentationAdapter)
