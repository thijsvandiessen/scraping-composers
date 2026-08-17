"""Stated scoring -> claims: the verbatim text, plus what can be queried.

"Which works are for piano" cannot be asked of prose, and an orchestral catalogue
does not even write prose — it writes a positional shorthand
(``3.2.2.3 - 2.2.3.0 - timp - strings[6]``). So whatever a page states about
scoring is recorded three ways over:

- verbatim, as the ``orchestration`` literal — what the page actually said, always;
- as ``written_for`` edges, for what the work is *for*: a scoring category
  (:mod:`.instrumentation`), or ``orchestra`` when the text is shorthand;
- as ``includes_instrument`` edges, for what is *in* the ensemble — the instruments
  a shorthand names (:mod:`.shorthand`).

The last two are separate predicates on purpose. A piano sonata is *for* piano; a
symphony merely *includes* one, and collapsing the two would put every symphony in
the answer to "works for piano" — the failure this layer exists to avoid.

Nothing here guesses. Text that is neither shorthand nor a recognised category
yields the literal alone and is counted, which is the queue for growing the tables.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from composer_schema import SourceClaim

from .instrumentation import category_for, members_of, parse_instrumentation
from .shorthand import Shorthand, parse_shorthand
from .values import coerce_value

#: Longest literal stored as a claim. Claims are for facts you can query and
#: compare; an open extractor will sooner or later hand back a whole programme
#: note as a "value", which belongs in the record's ``raw`` payload instead of in
#: a column other passes read.
MAX_CLAIM_VALUE_CHARS = 500

#: Predicates that carry a page's stated scoring, whichever way the model
#: expressed it — as the literal it was asked for, or as an edge it coined.
SCORING_PREDICATES = frozenset({"orchestration", "written_for"})

#: The predicate a shorthand's own text lands on, whatever the model called it.
_LITERAL = "orchestration"

#: What a shorthand says the work is for. Reading the notation is decoding, not
#: inferring: a page printing woodwind, brass and string desks has said "orchestra".
_ORCHESTRA = "orchestra"


@dataclass
class ScoringTarget:
    """Where a scoring's claims and structure are collected.

    Deliberately not :class:`~.claims._Subject` itself: this module needs three of
    its fields and nothing else, so the seam stays narrow enough that the two can
    be read apart.
    """

    claims: list[SourceClaim] = field(default_factory=list)
    long_values: dict[str, str] = field(default_factory=dict)
    scoring: dict[str, object] = field(default_factory=dict)


def add_literal(target: ScoringTarget, predicate: str, raw: str) -> None:
    """Record a literal claim, diverting one too long for the claims table."""
    value = coerce_value(predicate, raw)
    if len(value) > MAX_CLAIM_VALUE_CHARS:
        target.long_values[predicate] = value
        return
    target.claims.append(SourceClaim(predicate=predicate, value=value))


def _edge(predicate: str, category: str) -> SourceClaim:
    return SourceClaim(predicate=predicate, object_kind="instrumentation", object_label=category)


def _add_shorthand(target: ScoringTarget, parsed: Shorthand) -> None:
    """A parsed shorthand: the ensemble it is for, every instrument in it, and the
    counts kept as structure rather than as claims nobody would compare."""
    target.claims.append(_edge("written_for", _ORCHESTRA))
    target.claims.extend(_edge("includes_instrument", name) for name in parsed.instruments)
    target.scoring = parsed.as_dict()


def add_scoring(target: ScoringTarget, stated: str) -> str | None:
    """Record *stated* scoring on *target*; return what could not be read.

    The return value is the scoring phrase to count for review, or ``None`` when
    the text was understood. The verbatim literal is written either way, so a miss
    loses nothing beyond the ability to query it.
    """
    missed: str | None = None
    if (parsed := parse_shorthand(stated)) is not None:
        _add_shorthand(target, parsed)
    else:
        categories = parse_instrumentation(stated)
        target.claims.extend(_edge("written_for", name) for name in categories)
        # An ensemble is what the work is for; its instruments are members of it.
        target.claims.extend(_edge("includes_instrument", name) for name in members_of(categories))
        if not categories:
            missed = " ".join(stated.split())
    add_literal(target, _LITERAL, stated)
    return missed


def add_included_instrument(target: ScoringTarget, stated: str) -> str | None:
    """Record a model-stated ``includes_instrument`` after canonicalising it.

    Routed through the same table as everything else so the predicate can only ever
    name a scoring category, never the raw prose a local model put in
    ``object_label``. Unrecognised text is returned for counting rather than stored:
    the page's own words already survive in the record's verbatim facts.
    """
    if (category := category_for(stated)) is None:
        return " ".join(stated.split())
    target.claims.append(_edge("includes_instrument", category))
    return None
