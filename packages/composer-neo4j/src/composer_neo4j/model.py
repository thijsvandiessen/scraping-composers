"""The graph vocabulary: node labels and relationship types.

Gold is relational; Neo4j is a property graph. The translation follows one rule,
the same one that decides what belongs in a claim:

* **what a source asserted** — contested, provenanced, possibly disagreed on by
  two sources — becomes a **relationship**;
* **what we computed or hold one value of** becomes a **property**.

That is not only a modelling preference. Literal claims outnumber object claims
two to one (138,888 vs 67,919 in the current gold build), so keeping them as
properties is also what fits the export inside Aura's relationship cap.
"""

from __future__ import annotations

# entity kind -> node label. Kinds absent here are exported as :Entity, so a new
# kind appearing in gold degrades to a generic node instead of vanishing.
ENTITY_LABELS = {
    "person": "Person",
    "ensemble": "Ensemble",
    "place": "Place",
    "genre": "Genre",
    "movement": "Movement",
    "period": "Period",
    "profession": "Profession",
    "work": "Work",
}
DEFAULT_ENTITY_LABEL = "Entity"

WORK_LABEL = "Work"
CONCERT_LABEL = "Concert"
RECORDING_LABEL = "Recording"

# Every label that carries a unique `id` constraint.
NODE_LABELS = (
    *sorted(set(ENTITY_LABELS.values())),
    DEFAULT_ENTITY_LABEL,
    CONCERT_LABEL,
    RECORDING_LABEL,
)

# Claim predicates that point at another entity become relationships of this
# name. A predicate missing from the map is uppercased, so a new object claim
# still exports rather than being silently dropped.
CLAIM_RELATIONSHIPS = {
    "has_profession": "HAS_PROFESSION",
    "born_in": "BORN_IN",
    "died_in": "DIED_IN",
    "citizen_of": "CITIZEN_OF",
    "has_genre": "HAS_GENRE",
    "in_movement": "IN_MOVEMENT",
    "associated_period": "ASSOCIATED_PERIOD",
}

# participant role -> relationship type, shared by concerts and recordings.
# This is where the concert/recording duplication in the relational schema
# stops being duplication: same relationship types, different subject label.
PARTICIPANT_RELATIONSHIPS = {
    "conductor": "CONDUCTED_BY",
    "soloist": "PERFORMED_BY",
    "ensemble": "FEATURES",
}
DEFAULT_PARTICIPANT_RELATIONSHIP = "PERFORMED_BY"

COMPOSED_BY = "COMPOSED_BY"
PROGRAMMES = "PROGRAMMES"  # concert -> work
CONTAINS = "CONTAINS"  # recording -> work


def entity_label(kind: str) -> str:
    return ENTITY_LABELS.get(kind, DEFAULT_ENTITY_LABEL)


def claim_relationship(predicate: str) -> str:
    return CLAIM_RELATIONSHIPS.get(predicate, predicate.upper())


def participant_relationship(role: str) -> str:
    return PARTICIPANT_RELATIONSHIPS.get(role, DEFAULT_PARTICIPANT_RELATIONSHIP)
