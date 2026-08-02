"""The controlled vocabulary behind open-ended claim extraction.

The ``claims`` extract kind lets the model name its own predicate, which is what
makes it general: a page that states something no existing scraper models still
yields a claim. The cost is that one fact arrives under several names —
``composed``, ``year_composed``, ``date_of_composition`` — and ``claims`` has no
unique constraint, so gold would accumulate near-duplicate predicates that nobody
can query.

So the model is *guided* rather than constrained: :data:`VOCABULARY` is listed in
the prompt and reproduced here, :data:`ALIASES` folds the spellings that have
been seen in practice back onto it, and anything still unrecognised is kept but
counted, so a run reports which new predicates it met (see
:class:`~.resilience.ExtractStats`). Promoting a recurring newcomer into
``VOCABULARY`` or ``ALIASES`` is how the vocabulary is meant to grow.
"""

from __future__ import annotations

import re

#: Predicates already produced by the hand-written scrapers. Reusing these names
#: is the point of naming them in the prompt: a fact the LLM finds about a person
#: lands on the same predicate wikidata would have used for it.
_PERSON_PREDICATES = (
    "born_on",
    "died_on",
    "born_in",
    "died_in",
    "citizen_of",
    "has_profession",
    "has_genre",
    "in_movement",
    "has_function",
    "performs_as",
    "also_known_as",
    "musicbrainz_id",
)

#: Predicates this extractor introduces, for the work-level facts that concert
#: and recording pages state but neither existing schema has anywhere to put.
_WORK_PREDICATES = (
    # The edge from a composer to a work. Load-bearing: a work entity only
    # reaches gold if a kept person points at it (see composer_gold._claims), so
    # without this claim every other work predicate below is built in silver and
    # dropped at promote.
    "composed",
    "composed_in",
    "duration_minutes",
    "orchestration",
    "first_performed_on",
    "premiered_at",
    "dedicated_to",
    "program_note_by",
)

VOCABULARY: frozenset[str] = frozenset(_PERSON_PREDICATES + _WORK_PREDICATES)

#: Spellings folded onto a vocabulary term. Keys are already slugified, so only
#: genuine synonyms belong here — casing and punctuation are handled by
#: :func:`slugify`.
ALIASES: dict[str, str] = {
    "year_composed": "composed_in",
    "composition_year": "composed_in",
    "date_of_composition": "composed_in",
    "composition_date": "composed_in",
    "written_in": "composed_in",
    "date_composed": "composed_in",
    "length": "duration_minutes",
    "duration": "duration_minutes",
    "approximate_duration": "duration_minutes",
    "running_time": "duration_minutes",
    "instrumentation": "orchestration",
    "scoring": "orchestration",
    "scored_for": "orchestration",
    "premiere_date": "first_performed_on",
    "first_performance": "first_performed_on",
    "first_performed": "first_performed_on",
    "premiered_on": "first_performed_on",
    "world_premiere": "first_performed_on",
    "premiere_venue": "premiered_at",
    "dedication": "dedicated_to",
    "dedicatee": "dedicated_to",
    "program_note_author": "program_note_by",
    "notes_by": "program_note_by",
    "date_of_birth": "born_on",
    "birth_date": "born_on",
    "born": "born_on",
    "date_of_death": "died_on",
    "death_date": "died_on",
    "died": "died_on",
    "place_of_birth": "born_in",
    "birth_place": "born_in",
    "place_of_death": "died_in",
    "death_place": "died_in",
    "nationality": "citizen_of",
    "country": "citizen_of",
    "profession": "has_profession",
    "occupation": "has_profession",
    "role": "has_profession",
    "instrument": "performs_as",
    "plays": "performs_as",
    "voice_type": "performs_as",
    "genre": "has_genre",
    "also_called": "also_known_as",
    "aka": "also_known_as",
    "alias": "also_known_as",
}

#: Predicates a crawled page may never write, whatever the model returns.
#:
#: ``mentioned_in`` is provenance the ingest loop writes itself
#: (``composer_warehouse.ingestion.core._add_record_claims``). The four counts are
#: wikidata metrics, and ``sitelink_count`` in particular decides who enters gold
#: (``composer_gold._selection``) — a number invented from a fan page would
#: corrupt curation, so these are dropped rather than normalized.
DENYLIST: frozenset[str] = frozenset(
    {
        "mentioned_in",
        "sitelink_count",
        "statement_count",
        "identifier_count",
        "work_count",
    }
)

_NON_WORD = re.compile(r"[^\w]+")
_UNDERSCORES = re.compile(r"_{2,}")


def slugify(raw: str) -> str:
    """``"First LA Phil Performance"`` -> ``"first_la_phil_performance"``."""
    slug = _NON_WORD.sub("_", raw.strip().lower())
    return _UNDERSCORES.sub("_", slug).strip("_")


def normalize_predicate(raw: str) -> str | None:
    """The vocabulary term *raw* denotes, or ``None`` if it may not be stored.

    Returns a slugified predicate for anything outside the vocabulary rather than
    rejecting it — that is what keeps extraction open — so callers should treat a
    result outside :data:`VOCABULARY` as worth counting.
    """
    slug = slugify(raw)
    if not slug:
        return None
    slug = ALIASES.get(slug, slug)
    if slug in DENYLIST:
        return None
    return slug


#: For each vocabulary term whose object is another entity, the kind that entity
#: is. Every other term in :data:`VOCABULARY` takes a literal.
#:
#: The declaration matters because a local model fills the schema's ``value`` /
#: ``object_kind`` / ``object_label`` slots more or less at random — it will hand
#: back ``orchestration`` as an entity and ``has_profession`` as a string in the
#: same run. Deciding by predicate rather than by what the model chose mirrors
#: ``composer_scrapers.wikidata.parse.FIELDS``, which declares the same thing for
#: the hand-written path. A coined predicate has no declaration, so there the
#: model's own slots are all there is to go on.
OBJECT_KINDS: dict[str, str] = {
    "composed": "work",
    "born_in": "place",
    "died_in": "place",
    "citizen_of": "place",
    "has_profession": "profession",
    "has_genre": "genre",
    "in_movement": "movement",
    "dedicated_to": "person",
    "premiered_at": "place",
}


def object_kind_for(predicate: str) -> str | None:
    """The entity kind *predicate*'s object is, or ``None`` if it takes a literal
    or is a predicate nobody has declared."""
    return OBJECT_KINDS.get(predicate)


def takes_literal(predicate: str) -> bool:
    """Whether *predicate* is a vocabulary term whose object is a literal."""
    return predicate in VOCABULARY and predicate not in OBJECT_KINDS


#: Predicates that mean one thing about an entity and another about a literal.
#: "Composed" heads both an attribution ("Beethoven composed the Violin
#: Concerto") and a date row (the LA Phil's "At a Glance" block writes "Composed
#: 1806"), and only the object tells the two apart.
_LITERAL_FORMS = {"composed": "composed_in"}


def literal_form(predicate: str) -> str:
    """The predicate *predicate* denotes when its object is a literal."""
    return _LITERAL_FORMS.get(predicate, predicate)


def is_known(predicate: str) -> bool:
    """Whether *predicate* is part of the curated vocabulary."""
    return predicate in VOCABULARY


def vocabulary_hint() -> str:
    """The vocabulary as the prompt lists it, sorted so the prompt text — and so
    every cache key derived from it — stays stable across runs."""
    return ", ".join(sorted(VOCABULARY))
