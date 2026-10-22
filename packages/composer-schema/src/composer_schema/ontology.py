"""The claim vocabulary: every predicate a claim may carry, and what it means.

A claim's ``predicate`` is a plain string column, so nothing in the schema
stops a source from inventing one. This module is the place that does: each
predicate is listed once, with the kind of object it points at and one line
saying what it asserts. A scraper emitting a predicate that is not here fails
the scraper test suite (see that member's ``conftest.py``), so adding one is a
deliberate edit to this file rather than a side effect of a new adapter.

Predicates are grouped into themes. A theme is the unit a timeline or graph
view is meant to be built from: "who taught whom" reads the ``relation`` theme
and nothing else, instead of every connection the data happens to hold.

The table records the vocabulary as the scrapers use it today; it does not
rename or merge anything. Where usage is inconsistent (``has_genre`` is an
entity from Wikidata and a literal from IMSLP), the entry allows both and says
so, so the inconsistency is visible here rather than only in the data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from . import SourceClaim

#: The object "kind" of a claim whose object is a literal ``value`` rather than
#: another entity.
LITERAL = "literal"


class Theme(StrEnum):
    """What a group of predicates is about."""

    #: When and where a person was born and died.
    LIFE = "life"
    #: Person-to-person links: teaching, influence, family.
    RELATION = "relation"
    #: Who did what to a work: composing, writing its text, arranging, editing.
    AUTHORSHIP = "authorship"
    #: What a work is: scoring, key, genre, length, when it was written.
    WORK = "work"
    #: A printed edition of a work: publisher and catalogue details.
    EDITION = "edition"
    #: What a person is: profession, instrument, period, nationality, names.
    PERSON = "person"
    #: A person's history with one orchestra.
    PERFORMANCE = "performance"
    #: Counts and identifiers that rank or link entities but assert nothing
    #: about the music.
    BOOKKEEPING = "bookkeeping"


@dataclass(frozen=True)
class Predicate:
    """One entry in the vocabulary."""

    name: str
    theme: Theme
    #: Entity kinds the object may be, or :data:`LITERAL` for a plain value.
    objects: frozenset[str]
    meaning: str


def _p(name: str, theme: Theme, objects: str | tuple[str, ...], meaning: str) -> Predicate:
    kinds = (objects,) if isinstance(objects, str) else objects
    return Predicate(name, theme, frozenset(kinds), meaning)


_VOCABULARY: tuple[Predicate, ...] = (
    # life
    _p("born_on", Theme.LIFE, LITERAL, "birth date, to the precision the source gives (often a year)"),
    _p("died_on", Theme.LIFE, LITERAL, "death date, to the precision the source gives"),
    _p("born_in", Theme.LIFE, "place", "place of birth"),
    _p("died_in", Theme.LIFE, "place", "place of death"),
    # relation
    _p("student_of", Theme.RELATION, "person", "the subject studied with the object"),
    _p("teacher_of", Theme.RELATION, "person", "the subject taught the object"),
    _p("influenced_by", Theme.RELATION, "person", "the subject's work was influenced by the object"),
    _p("child_of", Theme.RELATION, "person", "the object is a parent of the subject"),
    _p("parent_of", Theme.RELATION, "person", "the object is a child of the subject"),
    _p("spouse_of", Theme.RELATION, "person", "the two were married"),
    _p("sibling_of", Theme.RELATION, "person", "the two are siblings"),
    # authorship
    _p("composed_by", Theme.AUTHORSHIP, "person", "the work was composed by the object"),
    _p("text_by", Theme.AUTHORSHIP, "person", "the work's text or libretto is by the object"),
    _p("arranged_by", Theme.AUTHORSHIP, "person", "this version of the work was arranged by the object"),
    _p("edited_by", Theme.AUTHORSHIP, "person", "the published edition was edited by the object"),
    _p("fingering_by", Theme.AUTHORSHIP, "person", "the edition's fingerings are by the object"),
    # work
    _p("composed_in", Theme.WORK, LITERAL, "year (or span) of composition"),
    _p("written_for", Theme.WORK, "instrumentation", "a scoring category the work is written for"),
    _p("includes_instrument", Theme.WORK, "instrumentation", "an instrument the scoring calls for"),
    _p("has_scoring", Theme.WORK, LITERAL, "the scoring as the source states it, verbatim"),
    _p("has_key", Theme.WORK, LITERAL, "the work's key"),
    _p(
        "has_genre",
        Theme.WORK,
        ("genre", LITERAL),
        "genre; an entity from Wikidata (on a person) but a literal from IMSLP (on a work)",
    ),
    _p("has_duration", Theme.WORK, LITERAL, "length in whole minutes"),
    _p("catalogue_number", Theme.WORK, LITERAL, "a catalogue or edition number (opus, BWV, HN ...)"),
    _p("part_of", Theme.WORK, "work", "the subject is one piece within the object (a volume or set)"),
    # edition
    _p("published_by", Theme.EDITION, "publisher", "an edition of the work is published by the object"),
    _p("edition_type", Theme.EDITION, LITERAL, "the kind of edition (score, parts, Urtext ...)"),
    _p("ismn", Theme.EDITION, LITERAL, "the edition's ISMN"),
    _p("isbn", Theme.EDITION, LITERAL, "the edition's ISBN"),
    _p("page_count", Theme.EDITION, LITERAL, "pages in the edition"),
    _p("difficulty_level", Theme.EDITION, LITERAL, "the publisher's difficulty grade"),
    # person
    _p("has_profession", Theme.PERSON, "profession", "what the person does (composer, conductor ...)"),
    _p("performs_as", Theme.PERSON, LITERAL, "an instrument or voice the person is credited with"),
    _p("has_function", Theme.PERSON, LITERAL, "a position held at an orchestra (chief conductor ...)"),
    _p("citizen_of", Theme.PERSON, "place", "country of citizenship"),
    _p("associated_period", Theme.PERSON, "period", "the musical period the person is filed under"),
    _p("in_movement", Theme.PERSON, "movement", "an artistic movement the person belonged to"),
    _p("also_known_as", Theme.PERSON, LITERAL, "another name the person is known by"),
    # performance
    _p("program_count", Theme.PERFORMANCE, LITERAL, "orchestra programmes featuring the person"),
    _p("first_season", Theme.PERFORMANCE, LITERAL, "the first season the orchestra performed with them"),
    _p("last_season", Theme.PERFORMANCE, LITERAL, "the latest season the orchestra performed with them"),
    # bookkeeping
    _p("sitelink_count", Theme.BOOKKEEPING, LITERAL, "Wikipedia articles about the entity (a fame proxy)"),
    _p("statement_count", Theme.BOOKKEEPING, LITERAL, "statements on the entity's Wikidata item"),
    _p("identifier_count", Theme.BOOKKEEPING, LITERAL, "external identifiers on the Wikidata item"),
    _p("work_count", Theme.BOOKKEEPING, LITERAL, "Wikidata items naming the person as composer"),
    _p("musicbrainz_id", Theme.BOOKKEEPING, LITERAL, "the MusicBrainz artist id"),
    _p("mentioned_in", Theme.BOOKKEEPING, LITERAL, "the page a source record came from (set by ingest)"),
)

PREDICATES: dict[str, Predicate] = {p.name: p for p in _VOCABULARY}


def predicates_in(theme: Theme) -> frozenset[str]:
    """The names of the predicates in *theme*."""
    return frozenset(p.name for p in _VOCABULARY if p.theme is theme)


class UnknownPredicateError(ValueError):
    """A claim uses a predicate the vocabulary does not list, or points it at
    a kind of object the vocabulary does not allow."""


def check_claim(claim: SourceClaim) -> None:
    """Raise :class:`UnknownPredicateError` unless *claim* fits the vocabulary."""
    entry = PREDICATES.get(claim.predicate)
    if entry is None:
        raise UnknownPredicateError(
            f"predicate {claim.predicate!r} is not in composer_schema.ontology; add it there with its meaning"
        )
    kind = claim.object_kind or LITERAL
    if kind not in entry.objects:
        allowed = ", ".join(sorted(entry.objects))
        raise UnknownPredicateError(
            f"{claim.predicate!r} points at {kind!r}; the vocabulary allows {allowed}"
        )
