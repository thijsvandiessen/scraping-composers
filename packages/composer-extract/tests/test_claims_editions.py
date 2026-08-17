"""Publisher-catalogue pages: edition facts, and scoring as a queryable edge.

The worked examples are a Henle edition page (one piece, its printing details)
and a Bärenreiter facet listing (many pieces, one scoring heading over them) —
the two page shapes a sheet-music catalogue is made of.
"""

from __future__ import annotations

from claims_harness import _entities, _run
from composer_extract.schema import ExtractedFact, PageClaimExtraction
from composer_schema import SourceClaim

#: A publisher's edition page as the model reads it: the attribution, the facts
#: about the piece, and the facts about the printed score.
_EDITION_PAGE = PageClaimExtraction(
    facts=[
        ExtractedFact(
            subject="Ludwig van Beethoven",
            subject_kind="person",
            predicate="composed",
            object_kind="work",
            object_label="Piano Sonata no. 14",
        ),
        ExtractedFact(
            subject="Piano Sonata no. 14",
            subject_kind="work",
            predicate="Instrumentation",
            value="Piano solo",
        ),
        ExtractedFact(
            subject="Piano Sonata no. 14", subject_kind="work", predicate="Key", value="C sharp minor"
        ),
        ExtractedFact(
            subject="Piano Sonata no. 14", subject_kind="work", predicate="Opus", value="op. 27 no. 2"
        ),
        ExtractedFact(
            subject="Piano Sonata no. 14",
            subject_kind="work",
            predicate="Publisher",
            object_kind="publisher",
            object_label="G. Henle Verlag",
        ),
        ExtractedFact(
            subject="Piano Sonata no. 14",
            subject_kind="work",
            predicate="Editor",
            object_kind="person",
            object_label="Norbert Gertsch",
        ),
        ExtractedFact(
            subject="Piano Sonata no. 14", subject_kind="work", predicate="Urtext", value="Urtext Edition"
        ),
    ]
)


def test_an_edition_page_yields_both_the_pieces_and_the_editions_facts() -> None:
    """A sheet-music page states two things at once, and both land on the work —
    there is no separate edition entity to hang the printing details off."""
    entities = _entities(_run(_EDITION_PAGE))

    work = entities["Ludwig van Beethoven: Piano Sonata no. 14"]
    assert work.kind == "work"
    assert set(work.claims) == {
        SourceClaim(predicate="orchestration", value="Piano solo"),
        SourceClaim(predicate="written_for", object_kind="instrumentation", object_label="piano"),
        SourceClaim(predicate="in_key", value="C sharp minor"),
        SourceClaim(predicate="catalogue_number", value="op. 27 no. 2"),
        SourceClaim(predicate="published_by", object_kind="publisher", object_label="G. Henle Verlag"),
        SourceClaim(predicate="edited_by", object_kind="person", object_label="Norbert Gertsch"),
        SourceClaim(predicate="edition_type", value="Urtext Edition"),
    }


def test_an_edition_page_still_attributes_the_work_to_its_composer() -> None:
    """Load-bearing for everything above it: gold seeds its walk from kept
    persons' claims, so a work no person points at strands all of these in silver."""
    composer = _entities(_run(_EDITION_PAGE))["Ludwig van Beethoven"]

    assert composer.claims == (
        SourceClaim(
            predicate="composed",
            object_kind="work",
            object_label="Ludwig van Beethoven: Piano Sonata no. 14",
        ),
    )


def test_stated_scoring_is_kept_verbatim_and_as_the_categories_it_names() -> None:
    """ "Which works are for piano" cannot be asked of prose, so the page's own
    words stay as the literal and the categories become edges beside it."""
    page = PageClaimExtraction(
        facts=[
            ExtractedFact(
                subject="Violin Sonata no. 5",
                subject_kind="work",
                predicate="Besetzung",
                value="Violine und Klavier",
            )
        ]
    )
    work = _entities(_run(page))["Violin Sonata no. 5"]

    assert set(work.claims) == {
        SourceClaim(predicate="orchestration", value="Violine und Klavier"),
        SourceClaim(predicate="written_for", object_kind="instrumentation", object_label="violin and piano"),
        SourceClaim(predicate="written_for", object_kind="instrumentation", object_label="violin"),
        SourceClaim(predicate="written_for", object_kind="instrumentation", object_label="piano"),
    }


def test_a_scoring_the_model_stated_as_an_entity_is_normalized_too() -> None:
    """A local model puts scoring in whichever slot it likes. Routing both slots
    through the same fold is what stops "for Piano solo" becoming an entity."""
    page = PageClaimExtraction(
        facts=[
            ExtractedFact(
                subject="Für Elise",
                subject_kind="work",
                predicate="written_for",
                object_kind="instrumentation",
                object_label="for Piano solo",
            )
        ]
    )
    work = _entities(_run(page))["Für Elise"]

    assert set(work.claims) == {
        SourceClaim(predicate="orchestration", value="for Piano solo"),
        SourceClaim(predicate="written_for", object_kind="instrumentation", object_label="piano"),
    }


def test_a_listing_page_scores_every_work_it_lists_under_the_same_heading() -> None:
    """Bärenreiter's "works for string orchestra" facet states one fact about each
    of the works under it, which is what makes a listing page worth crawling."""
    page = PageClaimExtraction(
        facts=[
            fact
            for title in ("Serenade for Strings", "Holberg Suite")
            for fact in (
                ExtractedFact(
                    subject=title, subject_kind="work", predicate="scoring", value="for string orchestra"
                ),
                ExtractedFact(
                    subject="Edvard Grieg",
                    subject_kind="person",
                    predicate="composed",
                    object_kind="work",
                    object_label=title,
                ),
            )
        ]
    )
    entities = _entities(_run(page))

    for title in ("Serenade for Strings", "Holberg Suite"):
        work = entities[f"Edvard Grieg: {title}"]
        assert (
            SourceClaim(
                predicate="written_for", object_kind="instrumentation", object_label="string orchestra"
            )
            in work.claims
        )
