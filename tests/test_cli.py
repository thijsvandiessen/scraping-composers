"""Tests for CLI query helpers (claim provenance lookup)."""

from sqlalchemy.orm import Session

from composer_ingest.cli import entity_claims
from composer_ingest.ingest import run_ingest
from composer_ingest.sources import SourceClaim
from test_ingest import FakeSource, person


def _ingest_two_sources_disagreeing(session: Session) -> None:
    # same person from two sources with a conflicting birth date and a shared one
    a = FakeSource(
        records=(
            person(
                "Abert, Johann Joseph",
                SourceClaim("has_profession", "profession", "composer"),
                SourceClaim("born_on", value="1832"),
                external_id="cg:990",
            ),
        ),
        NAME="concertgebouw",
    )
    b = FakeSource(
        records=(
            person(
                "Johann Joseph Abert",  # different formatting, same dedup key
                SourceClaim("has_profession", "profession", "composer"),
                SourceClaim("born_on", value="1832-09-20"),
                external_id="Q123",
            ),
        ),
        NAME="wikidata",
    )
    run_ingest(session, a)
    run_ingest(session, b)


def test_entity_claims_attributes_each_value_to_its_source(session: Session) -> None:
    _ingest_two_sources_disagreeing(session)

    ((entity, rows),) = entity_claims(session, "Abert, Johann Joseph")
    assert entity.label == "Abert, Johann Joseph"  # deduped to one entity

    born = [(value, source) for predicate, value, _obj, source, _rec in rows if predicate == "born_on"]
    assert born == [("1832", "concertgebouw"), ("1832-09-20", "wikidata")]


def test_entity_claims_filters_by_predicate_and_source(session: Session) -> None:
    _ingest_two_sources_disagreeing(session)

    ((_, rows),) = entity_claims(session, "Abert", predicate="born_on", source="wikidata")
    assert [(r[0], r[1], r[3]) for r in rows] == [("born_on", "1832-09-20", "wikidata")]


def test_entity_claims_carries_record_provenance(session: Session) -> None:
    _ingest_two_sources_disagreeing(session)

    ((_, rows),) = entity_claims(session, "Abert", predicate="born_on")
    # every row points back to the raw record it was extracted from
    assert all(record_id is not None for *_rest, record_id in rows)


def test_entity_claims_collapses_identical_assertions_from_one_source(session: Session) -> None:
    # one source asserting the same fact via two records keeps a single claim row
    source = FakeSource(
        records=(
            person(
                "Bach, Johann Sebastian",
                SourceClaim("has_profession", "profession", "composer"),
                external_id="a",
            ),
            person(
                "Johann Sebastian Bach",
                SourceClaim("has_profession", "profession", "composer"),
                external_id="b",
            ),
        ),
        NAME="wikidata",
    )
    run_ingest(session, source)

    ((_, rows),) = entity_claims(session, "Bach, Johann Sebastian", predicate="has_profession")
    assert len(rows) == 1
    predicate, _value, object_label, source_name, _rec = rows[0]
    assert (predicate, object_label, source_name) == ("has_profession", "composer", "wikidata")


def test_entity_claims_returns_empty_for_unknown_name(session: Session) -> None:
    assert entity_claims(session, "Nobody, At All") == []
