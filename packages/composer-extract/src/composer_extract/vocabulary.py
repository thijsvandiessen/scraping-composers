"""The controlled vocabulary behind open-ended claim extraction: the tables.

The terms live here and the functions that apply them in :mod:`.predicates`,
which re-exports everything below — the two were one module until the vocabulary
grew past what a 300-line file holds. Import from either; :mod:`.predicates` is
the older name and the one the rest of the package uses.

The ``claims`` extract kind lets the model name its own predicate, which is what
makes it general: a page that states something no existing scraper models still
yields a claim. The cost is that one fact arrives under several names —
``composed``, ``year_composed``, ``date_of_composition`` — and ``claims`` has no
unique constraint, so gold would accumulate near-duplicate predicates that nobody
can query.

So the model is *guided* rather than constrained: :data:`VOCABULARY` is listed in
the prompt, :data:`ALIASES` folds the spellings that have been seen in practice
back onto it, and anything still unrecognised is kept but counted, so a run
reports which new predicates it met (see
:class:`~.resilience.ExtractStats`). Promoting a recurring newcomer into
:data:`VOCABULARY` or :data:`ALIASES` is how the vocabulary is meant to grow.
"""

from __future__ import annotations

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

#: What a sheet-music publisher's catalogue states: facts about the piece
#: (scoring, key, catalogue number) and about the printed edition of it (who
#: edited it, who published it, what kind of edition it is). Edition facts are
#: claims on the *work* rather than on an "edition" entity of their own — two
#: editions of one piece therefore merge, which is the trade for not having to
#: dedup editions.
#:
#: ``written_for`` is not something the model is asked for: it is derived from
#: whatever scoring text lands on ``orchestration`` (see :mod:`.instrumentation`),
#: because a synonym table gets "Klavier zu vier Händen" right and a local model
#: does not.
_SCORE_PREDICATES = (
    "written_for",
    "in_key",
    "catalogue_number",
    "arrangement_of",
    "arranged_by",
    "part_of",
    "text_by",
    "published_by",
    "published_in",
    "edited_by",
    "fingering_by",
    "edition_type",
    "difficulty_level",
    "page_count",
    "ismn",
    "isbn",
)

#: Ensembles, institutions and teaching lineages — what an artist or orchestra
#: biography page states about who someone plays with and where.
_ORGANISATION_PREDICATES = (
    "member_of",
    "founded_in",
    "based_in",
    "student_of",
    "teacher_of",
    "recorded_on",
    "recorded_at",
)

VOCABULARY: frozenset[str] = frozenset(
    _PERSON_PREDICATES + _WORK_PREDICATES + _SCORE_PREDICATES + _ORGANISATION_PREDICATES
)

#: Spellings folded onto a vocabulary term. Keys are already slugified, so only
#: genuine synonyms belong here — casing and punctuation are handled by
#: :func:`~.predicates.slugify`.
#:
#: German spellings are carried because both publisher catalogues this vocabulary
#: was grown against (henle.de, baerenreiter.com) serve the same catalogue in two
#: languages, and a crawl restricted to ``/en/`` still meets the odd untranslated
#: field label.
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
    "besetzung": "orchestration",
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
    # The hand-written boosey scraper's spellings. Folding them in is what stops
    # one work described by both boosey and a crawl of its publisher from landing
    # on two predicates that never line up. (boosey's already-stored rows keep
    # their own names; rewriting those is a warehouse migration, not this table.)
    "has_scoring": "orchestration",
    "has_duration": "duration_minutes",
    "composed_by": "composed",
    # Edition credits.
    "editor": "edited_by",
    "edited": "edited_by",
    "herausgeber": "edited_by",
    "urtext_editor": "edited_by",
    "fingering": "fingering_by",
    "fingersatz": "fingering_by",
    "arranger": "arranged_by",
    "arrangement_by": "arranged_by",
    "bearbeiter": "arranged_by",
    "librettist": "text_by",
    "libretto_by": "text_by",
    "lyrics_by": "text_by",
    "words_by": "text_by",
    "publisher": "published_by",
    "verlag": "published_by",
    "imprint": "published_by",
    "publication_year": "published_in",
    "erscheinungsjahr": "published_in",
    # Identifiers. A publisher's order number and a composer's catalogue number
    # are the same kind of handle on the same piece, so they share a predicate.
    "opus": "catalogue_number",
    "opus_number": "catalogue_number",
    "catalogue": "catalogue_number",
    "catalog_number": "catalogue_number",
    "bwv": "catalogue_number",
    "kv": "catalogue_number",
    "koechel": "catalogue_number",
    "hn": "catalogue_number",
    "order_no": "catalogue_number",
    "order_number": "catalogue_number",
    "product_number": "catalogue_number",
    "article_number": "catalogue_number",
    "bestellnummer": "catalogue_number",
    # Edition shape.
    "key": "in_key",
    "tonality": "in_key",
    "tonart": "in_key",
    "urtext": "edition_type",
    "edition": "edition_type",
    "edition_form": "edition_type",
    "binding": "edition_type",
    "level_of_difficulty": "difficulty_level",
    "difficulty": "difficulty_level",
    "schwierigkeitsgrad": "difficulty_level",
    "pages": "page_count",
    "number_of_pages": "page_count",
    "seiten": "page_count",
    # Ensembles and lineage.
    "member": "member_of",
    "plays_with": "member_of",
    "founded": "founded_in",
    "founded_on": "founded_in",
    "based": "based_in",
    "studied_with": "student_of",
    "taught_by": "student_of",
    "taught": "teacher_of",
    "recording_date": "recorded_on",
    "recorded": "recorded_on",
    "recording_venue": "recorded_at",
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
#:
#: ``instrumentation`` is a scoring *category*, not a single instrument:
#: "piano", "string orchestra" and "violin and piano" are each one entity, which
#: is how the publishers themselves facet their catalogues.
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
    "written_for": "instrumentation",
    "arrangement_of": "work",
    "part_of": "work",
    "arranged_by": "person",
    "text_by": "person",
    "edited_by": "person",
    "fingering_by": "person",
    "published_by": "publisher",
    "member_of": "ensemble",
    "student_of": "person",
    "teacher_of": "person",
    "based_in": "place",
    "recorded_at": "place",
}

#: Predicates that mean one thing about an entity and another about a literal.
#: "Composed" heads both an attribution ("Beethoven composed the Violin
#: Concerto") and a date row (the LA Phil's "At a Glance" block writes "Composed
#: 1806"), and only the object tells the two apart.
LITERAL_FORMS: dict[str, str] = {"composed": "composed_in"}
