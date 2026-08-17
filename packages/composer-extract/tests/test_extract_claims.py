"""extract_claim_documents: page facts -> entity claims (no model needed).

The worked example throughout is the LA Phil's work page for Beethoven's Violin
Concerto, whose "At a Glance" block is what prompted open extraction.
"""

from __future__ import annotations

from claims_harness import NOW, URL, _entities, _mentions, _run
from composer_extract import ExtractOptions
from composer_extract.schema import ExtractedFact, PageClaimExtraction
from composer_schema import SourceClaim

#: The page as the model reads it: an attribution plus the At a Glance rows.
_LAPHIL_PAGE = PageClaimExtraction(
    facts=[
        ExtractedFact(
            subject="Ludwig van Beethoven",
            subject_kind="person",
            predicate="composed",
            object_kind="work",
            object_label="Violin Concerto",
        ),
        ExtractedFact(subject="Violin Concerto", subject_kind="work", predicate="Composed", value="1806"),
        ExtractedFact(
            subject="Violin Concerto", subject_kind="work", predicate="Length", value="c. 42 minutes"
        ),
        ExtractedFact(
            subject="Violin Concerto",
            subject_kind="work",
            predicate="Orchestration",
            value="flute, 2 oboes, 2 clarinets, 2 bassoons, 2 horns, 2 trumpets, timpani, strings",
        ),
        ExtractedFact(
            subject="Violin Concerto",
            subject_kind="work",
            predicate="program_note_by",
            value="Hugh Macdonald",
        ),
    ]
)


def test_a_work_page_yields_claims_on_a_composer_qualified_work_entity() -> None:
    entities = _entities(_run(_LAPHIL_PAGE))

    work = entities["Ludwig van Beethoven: Violin Concerto"]
    assert work.kind == "work"
    assert set(work.claims) == {
        SourceClaim(predicate="composed_in", value="1806"),
        SourceClaim(predicate="duration_minutes", value="42"),
        SourceClaim(
            predicate="orchestration",
            value="flute, 2 oboes, 2 clarinets, 2 bassoons, 2 horns, 2 trumpets, timpani, strings",
        ),
        SourceClaim(predicate="program_note_by", value="Hugh Macdonald"),
    }


def test_the_composed_edge_points_at_the_same_label_the_work_entity_carries() -> None:
    """Load-bearing: gold only keeps a work entity that a kept person's claim
    references, and the two only resolve to one entity if the labels match."""
    entities = _entities(_run(_LAPHIL_PAGE))

    composer = entities["Ludwig van Beethoven"]
    assert composer.kind == "person"
    assert composer.claims == (
        SourceClaim(
            predicate="composed", object_kind="work", object_label="Ludwig van Beethoven: Violin Concerto"
        ),
    )
    assert composer.claims[0].object_label in entities


def test_a_work_subject_also_yields_a_mention_so_it_resolves_to_a_canonical_work() -> None:
    (mention,) = _mentions(_run(_LAPHIL_PAGE))

    assert mention.title == "Violin Concerto"
    assert mention.composer == "Ludwig van Beethoven"
    # derive_concerts and derive_recordings both ignore this marker.
    assert mention.raw["_source"] == "llm"
    assert mention.raw["_kind"] == "work_profile"


def test_an_unqualified_work_keeps_its_bare_title() -> None:
    """No composed edge on the page means no composer to qualify with; the title
    stands on its own rather than gaining an empty prefix."""
    page = PageClaimExtraction(
        facts=[
            ExtractedFact(
                subject="Symphonie fantastique", subject_kind="work", predicate="Length", value="55 minutes"
            )
        ]
    )
    entities = _entities(_run(page))

    assert "Symphonie fantastique" in entities
    (mention,) = _mentions(_run(page))
    assert (mention.title, mention.composer) == ("Symphonie fantastique", None)


def test_facts_about_people_become_claims_the_scrapers_already_use() -> None:
    page = PageClaimExtraction(
        facts=[
            ExtractedFact(
                subject="Gustavo Dudamel",
                subject_kind="person",
                predicate="Date of Birth",
                value="1981-01-26",
            ),
            ExtractedFact(
                subject="Gustavo Dudamel",
                subject_kind="person",
                predicate="occupation",
                object_kind="profession",
                object_label="conductor",
            ),
        ]
    )
    entities = _entities(_run(page))

    assert set(entities["Gustavo Dudamel"].claims) == {
        SourceClaim(predicate="born_on", value="1981-01-26"),
        SourceClaim(predicate="has_profession", object_kind="profession", object_label="conductor"),
    }


def test_denylisted_predicates_never_become_claims() -> None:
    """sitelink_count decides who enters gold; a page may not write it."""
    page = PageClaimExtraction(
        facts=[
            ExtractedFact(
                subject="Gustavo Dudamel", subject_kind="person", predicate="sitelink_count", value="400"
            ),
            ExtractedFact(
                subject="Gustavo Dudamel", subject_kind="person", predicate="born_on", value="1981-01-26"
            ),
        ]
    )
    entities = _entities(_run(page))

    assert entities["Gustavo Dudamel"].claims == (SourceClaim(predicate="born_on", value="1981-01-26"),)


def test_a_coined_predicate_is_kept_and_counted_for_review() -> None:
    options = ExtractOptions(now=NOW)
    page = PageClaimExtraction(
        facts=[
            ExtractedFact(
                subject="Violin Concerto",
                subject_kind="work",
                predicate="First LA Phil Performance",
                value="December 5, 1919",
            )
        ]
    )
    entities = _entities(_run(page, options=options))

    assert entities["Violin Concerto"].claims == (
        SourceClaim(predicate="first_la_phil_performance", value="December 5, 1919"),
    )
    assert options.stats.unknown_predicates == {"first_la_phil_performance": 1}
    assert options.stats.unknown_summary() == "first_la_phil_performance(1)"


def test_a_repeated_fact_becomes_one_claim() -> None:
    """A model asked for everything on a page states some things twice, and the
    claims table has no unique constraint to catch it downstream."""
    fact = ExtractedFact(subject="Violin Concerto", subject_kind="work", predicate="Composed", value="1806")
    options = ExtractOptions(now=NOW)
    entities = _entities(_run(PageClaimExtraction(facts=[fact, fact]), options=options))

    assert entities["Violin Concerto"].claims == (SourceClaim(predicate="composed_in", value="1806"),)
    assert options.stats.claims == 1


def test_a_prose_value_goes_to_raw_rather_than_into_a_claim() -> None:
    """Claims are for facts you can compare; a whole programme note is not one."""
    note = "The four drum taps that open Beethoven's Violin Concerto " * 20
    page = PageClaimExtraction(
        facts=[
            ExtractedFact(
                subject="Violin Concerto", subject_kind="work", predicate="program_note", value=note
            )
        ]
    )
    entities = _entities(_run(page))

    work = entities["Violin Concerto"]
    assert work.claims == ()
    assert work.raw["long_values"]["program_note"] == note.strip()


def test_a_page_stating_nothing_yields_nothing() -> None:
    assert _run(PageClaimExtraction()) == []


def test_an_orchestral_scoring_list_stays_a_literal_and_is_counted() -> None:
    """The LA Phil's orchestration row is a list of sections. Reading it as a work
    for flute would be worse than leaving it unread, so it is left — and reported,
    which is how the category table grows."""
    options = ExtractOptions(now=NOW)
    scoring = "flute, 2 oboes, 2 clarinets, 2 bassoons, 2 horns, 2 trumpets, timpani, strings"
    entities = _entities(_run(_LAPHIL_PAGE, options=options))

    work = entities["Ludwig van Beethoven: Violin Concerto"]
    assert SourceClaim(predicate="orchestration", value=scoring) in work.claims
    assert not [c for c in work.claims if c.predicate == "written_for"]
    assert options.stats.unrecognised_scoring == {scoring: 1}
    assert options.stats.unrecognised_summary().startswith("flute, 2 oboes")


def test_the_page_url_and_verbatim_facts_travel_in_raw() -> None:
    work = _entities(_run(_LAPHIL_PAGE))["Ludwig van Beethoven: Violin Concerto"]

    assert work.raw["url"] == URL
    assert work.url == URL
    assert [f["predicate"] for f in work.raw["facts"]] == [
        "Composed",
        "Length",
        "Orchestration",
        "program_note_by",
    ]
