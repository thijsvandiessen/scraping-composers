"""The genre and movement vocabulary's fixed points (#349).

Wikidata's genres hang off each other through ``subclass of`` (P279), which the
``wikidata_genres`` source stores as ``subclass_of`` claims. The roots of that
graph are too abstract to browse by ("music", "art genre"), so a handful of
well-known genres and periods are picked here as the top level: the facets the
consumer API offers, and the targets the orphan report measures paths to. A
genre with no ``subclass_of`` path to one of these is an orphan.

Keyed by QID, so a Wikidata rename changes nothing here; the label is only a
reminder for the reader. The lists live in ``composer_models`` because every
tier that reads them (warehouse, consumer API) already depends on it.
"""

from __future__ import annotations

#: Top-level genres, as ``QID: English label``.
TOP_LEVEL_GENRES: dict[str, str] = {
    "Q9730": "classical music",
    "Q1344": "opera",
    "Q189201": "chamber music",
    "Q1065742": "religious music",
    "Q316930": "dance music",
    "Q43343": "folk music",
    "Q373342": "popular music",
    "Q11399": "rock music",
    "Q8341": "jazz",
    "Q9778": "electronic music",
    "Q492264": "film score",
}

#: Top-level movements: the periods of Western art music, plus the broad
#: movements composers' P135 most often names outright. "classical music" is
#: one of them: it is the movement Wikidata tags most composers with, and it is
#: a top-level genre as well (#364).
TOP_LEVEL_MOVEMENTS: dict[str, str] = {
    "Q9730": "classical music",
    "Q163775": "medieval music",
    "Q201405": "Renaissance music",
    "Q8361": "Baroque music",
    "Q17723": "Classical period",
    "Q207591": "Romantic music",
    "Q37068": "Romanticism",
    "Q878985": "modernism",
    "Q1338153": "20th-century classical music",
    "Q612024": "contemporary classical music",
}

TOP_LEVEL: dict[str, dict[str, str]] = {"genre": TOP_LEVEL_GENRES, "movement": TOP_LEVEL_MOVEMENTS}

#: The ``genre_type`` claim value ``wikidata_genres`` gives a musical form
#: ("symphony", "concerto"). Forms are an axis of their own, not a genre that
#: failed to reach the top level, so the orphan report counts them apart.
MUSICAL_FORM = "musical form"

#: The predicate that tags a person with an entity of each kind.
MEMBERSHIP_PREDICATES: dict[str, str] = {"genre": "has_genre", "movement": "in_movement"}

GENRE_KINDS = tuple(TOP_LEVEL)
