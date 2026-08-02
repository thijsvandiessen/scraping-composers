"""Reading what the model actually returned, as opposed to what it was asked for.

:class:`~.schema.ExtractedFact` offers three slots for a fact's object — ``value``
for a literal, ``object_kind`` + ``object_label`` for an entity — and a local
model fills them more or less at random: ``orchestration`` comes back as an
entity, ``has_profession`` as a bare string, and a work page headed by its title
leads the model to state ``composed`` backwards. None of that is worth failing a
page over, and none of it is knowable from one fact in isolation.

So this module reconciles a page's facts against each other and against the
declarations in :mod:`.predicates` before :mod:`.claims` turns them into claims.
Every repair here was observed in real output; each one is pinned by a test.
"""

from __future__ import annotations

from collections.abc import Iterable

from .predicates import normalize_predicate, object_kind_for, takes_literal
from .schema import ExtractedFact

PERSON_KIND = "person"
WORK_KIND = "work"

#: Predicates that can only be said of a piece of music. A page stating any of
#: them about a subject has settled what that subject is, whatever ``subject_kind``
#: the model guessed — nobody is scored for two oboes.
_WORK_PREDICATES = frozenset({"composed_in", "duration_minutes", "orchestration", "first_performed_on"})


def entity_kind(raw: str | None, default: str = PERSON_KIND) -> str:
    return (raw or "").strip().lower() or default


def title_key(title: str) -> str:
    """Loose title identity, used only to line a work's ``composed`` edge up with
    the statements made about that same work elsewhere on the page. Kept local
    rather than reusing the warehouse's ``dedup_key``, which seeds entity uuids
    and must not shift underneath them."""
    return " ".join(title.split()).casefold()


def _flip(fact: ExtractedFact) -> ExtractedFact:
    """A ``composed`` edge stated the wrong way round, turned around.

    ``composed`` runs composer -> work, and gold's walk depends on it: it seeds
    from the claims of kept *persons*, so an edge pointing work -> composer
    strands the work's own facts in silver. A model states the relation either
    way round depending on how the page is worded — the LA Phil's work pages lead
    with the title — so the direction is repaired here rather than left to the
    prompt alone.
    """
    return fact.model_copy(
        update={
            "subject": (fact.object_label or "").strip(),
            "subject_kind": PERSON_KIND,
            "object_kind": WORK_KIND,
            "object_label": fact.subject.strip(),
        }
    )


def _work_titles(facts: Iterable[ExtractedFact]) -> set[str]:
    """Every name the page treats as a work: what it attributes to a composer
    (whichever way round it stated the attribution), and what it says something
    only a work can be the subject of."""
    titles: set[str] = set()
    for fact in facts:
        predicate = normalize_predicate(fact.predicate)
        if predicate == "composed" and fact.object_label:
            titles.add(title_key(fact.object_label))
        elif predicate in _WORK_PREDICATES and fact.subject.strip():
            titles.add(title_key(fact.subject))
    return titles


def repair(facts: Iterable[ExtractedFact]) -> list[ExtractedFact]:
    """Correct the two things a model gets wrong about attribution.

    First the direction of ``composed`` (see :func:`_flip`); then ``subject_kind``,
    which arrives as the default "person" even for a work. The page's own
    attributions say which names are works, and that is better evidence than the
    kind the model guessed fact by fact.
    """
    directed = [
        _flip(fact)
        if normalize_predicate(fact.predicate) == "composed"
        and entity_kind(fact.object_kind, "") == PERSON_KIND
        else fact
        for fact in facts
    ]
    titles = _work_titles(directed)
    return [
        fact.model_copy(update={"subject_kind": WORK_KIND}) if title_key(fact.subject) in titles else fact
        for fact in directed
    ]


def stated(fact: ExtractedFact) -> str | None:
    """What the fact says its object is, from whichever slot the model used."""
    return (fact.value or fact.object_label or "").strip() or None


def object_kind(fact: ExtractedFact, predicate: str) -> str | None:
    """The kind this fact's object is an entity of, or ``None`` for a literal.

    A declared predicate decides for itself (see
    :data:`~.predicates.OBJECT_KINDS`); only a coined one is left to whatever the
    model put in ``object_kind``.
    """
    if (declared := object_kind_for(predicate)) is not None:
        return declared
    if takes_literal(predicate):
        return None
    return entity_kind(fact.object_kind, "") or None
