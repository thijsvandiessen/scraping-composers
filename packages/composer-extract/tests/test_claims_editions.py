"""Publisher-catalogue pages: edition facts, and scoring as a queryable edge.

The worked examples are a Henle edition page (one piece, its printing details)
and a Bärenreiter facet listing (many pieces, one scoring heading over them) —
the two page shapes a sheet-music catalogue is made of.
"""

from __future__ import annotations

from claims_harness import NOW, _entities, _run
from composer_extract import ExtractOptions
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


#: An orchestral catalogue's work page: the scoring is a positional shorthand, not
#: prose. (Beethoven 5 as Boosey & Hawkes prints it.)
_ORCHESTRAL_PAGE = PageClaimExtraction(
    facts=[
        ExtractedFact(
            subject="Ludwig van Beethoven",
            subject_kind="person",
            predicate="composed",
            object_kind="work",
            object_label="Symphony No. 5",
        ),
        ExtractedFact(
            subject="Symphony No. 5",
            subject_kind="work",
            predicate="Instrumentation",
            value="3.2.2.3 - 2.2.3.0 - timp - strings[6]",
        ),
    ]
)

_WORK = "Ludwig van Beethoven: Symphony No. 5"


def _claims(predicate: str) -> set[str | None]:
    work = _entities(_run(_ORCHESTRAL_PAGE))[_WORK]
    return {claim.object_label for claim in work.claims if claim.predicate == predicate}


def test_a_shorthand_says_the_work_is_for_orchestra() -> None:
    """Reading the notation is decoding, not inferring: a page printing woodwind,
    brass and string desks has said "orchestra"."""
    assert _claims("written_for") == {"orchestra"}


def test_a_shorthand_names_its_instruments_as_included_not_as_what_it_is_for() -> None:
    """The split the predicates exist for. A symphony contains a flute; it is not a
    work *for* flute, and one predicate for both would put every symphony in the
    answer to "works for piano"."""
    assert _claims("includes_instrument") == {
        "flute",
        "oboe",
        "clarinet",
        "bassoon",
        "horn",
        "trumpet",
        "trombone",
        "timpani",
        "strings",
    }


def test_a_shorthand_still_keeps_the_publishers_own_text() -> None:
    work = _entities(_run(_ORCHESTRAL_PAGE))[_WORK]

    assert (
        SourceClaim(predicate="orchestration", value="3.2.2.3 - 2.2.3.0 - timp - strings[6]") in work.claims
    )


def test_a_shorthands_counts_travel_in_raw_rather_than_as_claims() -> None:
    """Player counts and the string-part number are structure for later analysis,
    not facts to compare, so they stay out of the claims table."""
    work = _entities(_run(_ORCHESTRAL_PAGE))[_WORK]

    scoring = work.raw["scoring"]
    assert scoring["counts"]["flute"] == 3
    assert scoring["string_parts"] == 6
    assert not [c for c in work.claims if c.predicate in {"string_parts", "counts"}]


def test_a_shorthand_is_not_counted_as_unrecognised_scoring() -> None:
    options = ExtractOptions(now=NOW)
    _run(_ORCHESTRAL_PAGE, options=options)

    assert options.stats.unrecognised_scoring == {}


def test_a_model_stated_instrument_is_canonicalised_before_it_becomes_an_entity() -> None:
    """A local model puts whatever it likes in ``object_label``. Routing the
    predicate through the same table is what stops "2 Klarinetten in B" minting an
    entity of its own."""
    page = PageClaimExtraction(
        facts=[
            ExtractedFact(
                subject="Symphony No. 5",
                subject_kind="work",
                predicate="instruments",
                object_kind="instrumentation",
                object_label="Klavier",
            ),
            ExtractedFact(
                subject="Symphony No. 5",
                subject_kind="work",
                predicate="instruments",
                object_kind="instrumentation",
                object_label="2 Klarinetten in B",
            ),
        ]
    )
    options = ExtractOptions(now=NOW)
    work = _entities(_run(page, options=options))["Symphony No. 5"]

    assert work.claims == (
        SourceClaim(predicate="includes_instrument", object_kind="instrumentation", object_label="piano"),
    )
    assert options.stats.unrecognised_scoring == {"2 Klarinetten in B": 1}
