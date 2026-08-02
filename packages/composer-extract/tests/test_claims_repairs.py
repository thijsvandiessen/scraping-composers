"""Repairs for what a local model actually returns.

Every case here was observed running qwen2.5 over laphil.com pages; the schema
gives the model three object slots and it fills them more or less at random.
"""

from __future__ import annotations

from claims_harness import _entities, _run
from composer_extract.schema import ExtractedFact, PageClaimExtraction
from composer_schema import SourceClaim


def test_a_literal_in_the_object_slot_is_still_a_literal() -> None:
    """The commonest shape by far: the fact is right, but the value arrived in
    ``object_label`` with no ``object_kind`` beside it."""
    page = PageClaimExtraction(
        facts=[
            ExtractedFact(
                subject="Overture to Don Giovanni",
                subject_kind="work",
                predicate="Composed",
                object_label="1786",
            )
        ]
    )
    assert _entities(_run(page))["Overture to Don Giovanni"].claims == (
        SourceClaim(predicate="composed_in", value="1786"),
    )


def test_a_declared_predicate_ignores_the_kind_the_model_guessed() -> None:
    """duration_minutes came back as an entity object of kind "work". The
    predicate decides what its object is, not the model."""
    page = PageClaimExtraction(
        facts=[
            ExtractedFact(
                subject="Concerto for String Orchestra",
                subject_kind="work",
                predicate="duration_minutes",
                object_kind="work",
                object_label="c. 23",
            )
        ]
    )
    assert _entities(_run(page))["Concerto for String Orchestra"].claims == (
        SourceClaim(predicate="duration_minutes", value="23"),
    )


def test_a_backwards_composed_edge_is_turned_around() -> None:
    """A page headed by the title leads the model to state the work as the
    subject. Left alone the edge runs work -> composer, and gold's walk (which
    seeds from kept persons) would never reach the work."""
    page = PageClaimExtraction(
        facts=[
            ExtractedFact(
                subject="Overture to Don Giovanni",
                subject_kind="person",
                predicate="composed",
                object_kind="person",
                object_label="Wolfgang Amadeus Mozart",
            )
        ]
    )
    entities = _entities(_run(page))

    assert entities["Wolfgang Amadeus Mozart"].kind == "person"
    assert entities["Wolfgang Amadeus Mozart"].claims == (
        SourceClaim(
            predicate="composed",
            object_kind="work",
            object_label="Wolfgang Amadeus Mozart: Overture to Don Giovanni",
        ),
    )


def test_a_subject_of_work_only_facts_is_typed_as_a_work() -> None:
    """subject_kind arrives as the default "person" even for a piece of music.
    Nobody is scored for two oboes, so the predicates settle it."""
    page = PageClaimExtraction(
        facts=[
            ExtractedFact(
                subject="Overture to Don Giovanni",
                subject_kind="person",
                predicate="Orchestration",
                object_label="2 flutes, 2 oboes",
            ),
            ExtractedFact(
                subject="Overture to Don Giovanni",
                subject_kind="person",
                predicate="Length",
                object_label="c. 6 minutes",
            ),
        ]
    )
    entities = _entities(_run(page))

    assert entities["Overture to Don Giovanni"].kind == "work"
    assert set(entities["Overture to Don Giovanni"].claims) == {
        SourceClaim(predicate="orchestration", value="2 flutes, 2 oboes"),
        SourceClaim(predicate="duration_minutes", value="6"),
    }


def test_a_coined_predicate_keeps_the_slot_the_model_chose() -> None:
    """Nothing declares what a coined predicate's object is, so the model's own
    ``object_kind`` is the only evidence there is."""
    page = PageClaimExtraction(
        facts=[
            ExtractedFact(
                subject="Overture to Don Giovanni",
                subject_kind="work",
                predicate="performed_by",
                object_kind="ensemble",
                object_label="Los Angeles Philharmonic",
            )
        ]
    )
    assert _entities(_run(page))["Overture to Don Giovanni"].claims == (
        SourceClaim(
            predicate="performed_by", object_kind="ensemble", object_label="Los Angeles Philharmonic"
        ),
    )
