"""Tests for the uniform Document, its factories, hashing and bucket round-trip."""

from __future__ import annotations

from composer_ingest.document import (
    SourceClaim,
    content_hash,
    entity_document,
    stamp,
    work_mention_document,
)
from composer_ingest.raw_fetch import _deserialize, _serialize


def test_entity_document_shape() -> None:
    doc = entity_document(
        id="Q255",
        name="Beethoven",
        url="https://x/Q255",
        claims=(SourceClaim("has_profession", "profession", "composer"),),
        raw={"k": "v"},
    )
    assert doc.doc_type == "entity"
    assert doc.id == "Q255"
    assert doc.url == "https://x/Q255"
    assert doc.body["name"] == "Beethoven"
    assert doc.body["kind"] == "person"
    assert doc.body["claims"] == [
        {
            "predicate": "has_profession",
            "object_kind": "profession",
            "object_label": "composer",
            "value": None,
        }
    ]
    assert doc.body["raw"] == {"k": "v"}
    # not stamped yet
    assert doc.source_name == "" and doc.ingested_at == "" and doc.content_hash == ""


def test_work_mention_document_shape() -> None:
    doc = work_mention_document(id="m1", title="Symphony No. 5", composer="Beethoven", raw={"d": 1})
    assert doc.doc_type == "work_mention"
    assert doc.body == {"title": "Symphony No. 5", "composer": "Beethoven", "raw": {"d": 1}}


def test_content_hash_is_stable_and_order_independent() -> None:
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})


def test_content_hash_changes_with_content() -> None:
    assert content_hash({"a": 1}) != content_hash({"a": 2})


def test_stamp_fills_base_fields_and_hashes_body_only() -> None:
    doc = entity_document(id="1", name="Mozart")
    stamped = stamp(doc, "imslp")
    assert stamped.source_name == "imslp"
    assert stamped.ingested_at  # set to an ISO timestamp
    assert stamped.content_hash == content_hash(doc.body)


def test_stamp_keeps_existing_ingested_at() -> None:
    doc = entity_document(id="1", name="Mozart")
    once = stamp(doc, "imslp")
    twice = stamp(once, "imslp")
    assert twice.ingested_at == once.ingested_at  # not overwritten on re-stamp


def test_bucket_round_trip() -> None:
    doc = stamp(
        entity_document(
            id="1",
            name="Mozart",
            claims=(SourceClaim("born_on", value="1756"),),
            raw={"x": [1, 2]},
        ),
        "imslp",
    )
    assert _deserialize(_serialize(doc)) == doc
