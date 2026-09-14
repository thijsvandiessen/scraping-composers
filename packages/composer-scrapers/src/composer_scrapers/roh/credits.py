"""Telling a credited person from a stated fact, on a page that renders both alike.

Every record page is one ``<th>label</th><td>value</td>`` table in which
"Composer / Giuseppe Verdi" and "Language / Italian" are the same markup. Only
the label separates them, so the label vocabulary is the parser.

**It is an allowlist, and that is the point.** The tempting rule is the other
one — treat every row as a credit unless it is a known field — and it fails
badly in one direction: a label this parser has never seen becomes a person, and
"Opera in four acts" is filed as a human being with a name. Under an allowlist
the same unknown label is merely a row that is not read as a credit, which is
the harmless direction.

Note what "not read as a credit" costs, because it is less than it sounds:
:func:`read` keeps every row it does not credit as a **field**, under the label
the page printed. So nothing is dropped, ever — an unrecognised row lands in
``fields`` and rides along in the document's ``raw`` either way. The per-level
``FIELDS`` sets are therefore not what preserves the data; they are only the
"we have already looked at this label" register that keeps
:func:`unknown_labels` quiet. A label in neither set is reported by the adapter
at the end of a sweep, which is how both lists grow.

The lists were read off the pages rather than guessed — a sweep of the work
tier and a sample across the two below it — which is why they hold things nobody
would think to write down: "Combat sequences director", "Choreography after",
"Recording artist", "Devised and produced by". It is also why the near-synonyms
are all there. "Choreographer" and "Choreography" are one credit filed by two
cataloguers, as are "Set designer" and "Designer"; :func:`role` folds them, so a
person is not split in two by which template wrote them.

**Qualifiers are stripped, not enumerated.** A staging credit is rarely just
"Director": the database has "Assistant director", "Revival director",
"Associate director", "Revival associate director", and the same fan-out for
choreographers, designers and conductors. Listing every combination would be a
hundred entries that still missed the next one, so :func:`role` peels the
qualifiers in :data:`_QUALIFIERS` off the front and matches what remains — the
allowlist holds "director" once. The printed label is kept verbatim on the
credit, so "Revival associate director" is still what the record says; only the
lookup is simplified. Qualifiers are peeled off the *front* only, which is why
"Combat sequences director" stays its own role: it is a different job, not a
qualified one.

**A profession is claimed for very few of them.** :data:`PROFESSIONS` maps only
the roles that state what someone *is* in a way the warehouse can use —
composer, conductor, choreographer, librettist and the rest of the small set. A
lighting designer is recorded as having lit a production, in the credit itself,
and no profession is claimed for them: this source is an authority on who was
billed, not on what anyone's career was. **A person becomes a composer here only
because a work page filed them under "Composer".**
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .text import credit as _split_credit
from .text import pairs

_SLUG_RE = re.compile(r"[^a-z0-9]+")

#: Leading fragments that qualify a credit without changing what the job is.
#: Peeled repeatedly, so "revival_associate_director" reaches "director" and
#: "assistant_to_the_choreographer" reaches "choreographer". Prefixes, not single
#: tokens: "to_the" has to come off whole, and "the" alone must never come off.
#: :func:`role` also drops a trailing "by" — "Devised by", "Scenario by" and
#: "Devised and produced by" are the same grammar, and dropping it means the
#: allowlist holds the job rather than the preposition.
_QUALIFIERS = (
    "additional",
    "assistant",
    "associate",
    "guest",
    "original",
    "revival",
    "to_the",
)

#: Labels that mean the same credit under different house styles. Folded after
#: the qualifiers are peeled, so one person is not two.
_SYNONYMS = {
    # "Choreography after: Marius Petipa" credits Petipa's choreography, so the
    # person named is a choreographer; the printed label keeps the distinction.
    "choreography_after": "choreographer",
    "choreography": "choreographer",
    # "Assistant to the choreographers" — the peel leaves the plural behind.
    "choreographers": "choreographer",
    "chorus_director": "chorus_master",
    "artistic_director_of_the_chorus": "chorus_master",
    # "Co-" is folded here rather than peeled as a qualifier: peeling it would
    # also turn "Co-production with" into a production credit, and that row
    # names an opera house.
    "co_concert_master": "concert_master",
    "adaptation": "adapted",
    # Compound credits claim the lesser of the two professions on purpose:
    # "Arranger and orchestrator" makes Rimsky-Korsakov an arranger here and
    # leaves the second claim to a source that states it on its own.
    "arranger_and_additional_music": "arranger",
    "arranger_and_orchestrator": "arranger",
    "costumes_realised": "costumes_realized",
    "designer": "set_designer",
    "low_voice_arrangement": "arranger",
    "sound_design": "sound_designer",
    "designs": "set_designer",
    "dramaturgy": "dramaturg",
    "flamenco_choreographer": "choreographer",
    "libretto": "librettist",
    "movement": "movement_director",
    "music": "composer",
    "recording_artists": "recording_artist",
    "staged": "staging",
    "text": "author",
    "translation": "translator",
    "video_designs": "video_designer",
}


#: Roles that state a profession worth claiming. Everything credited outside this
#: set is still recorded as a credit; it just does not make anyone a "lighting
#: designer" in the entity graph. See the module docstring.
PROFESSIONS = {
    "arranger": "arranger",
    "author": "writer",
    "choreographer": "choreographer",
    "composer": "composer",
    "conductor": "conductor",
    "director": "director",
    "librettist": "librettist",
    "lyricist": "lyricist",
    "orchestrator": "orchestrator",
    "playwright": "playwright",
    "poet": "poet",
    "translator": "translator",
    "writer": "writer",
}


@dataclass(frozen=True)
class Credit:
    """One person or ensemble, and what they were credited as.

    ``label`` is the row as printed ("Revival lighting designer"); ``role`` is
    that folded to a slug and through :data:`_SYNONYMS`. ``note`` is the
    qualifier the site hangs off a ``<br>`` — "(1995 revival)", "For the
    Mariinsky Theatre, 1900" — which is about the credit, not the person.
    """

    role: str
    label: str
    name: str
    note: str | None = None

    @property
    def profession(self) -> str | None:
        """The profession this credit supports, or None if it supports none."""
        return PROFESSIONS.get(self.role)


def role(label: str) -> str:
    """*label* as a slug, with leading qualifiers peeled and synonyms folded.

    "Revival associate director" and "Director" both come back as ``director``,
    and "Scenario by" as ``scenario``; "Combat sequences director" comes back as
    itself. A label that is *only* a qualifier ("Revival", "By") keeps its slug
    rather than peeling away to nothing.
    """
    slug = _SLUG_RE.sub("_", label.strip().casefold()).strip("_")
    peeling = True
    while peeling:
        peeling = False
        for qualifier in _QUALIFIERS:
            prefix = f"{qualifier}_"
            if slug.startswith(prefix) and len(slug) > len(prefix):
                slug = slug[len(prefix) :]
                peeling = True
                break
    if slug.endswith("_by"):
        slug = slug[: -len("_by")]
    return _SYNONYMS.get(slug, slug)


def read(fragment: str, known: frozenset[str]) -> tuple[tuple[Credit, ...], dict[str, str]]:
    """Split a detail table into its credits and its plain fields.

    *known* is the set of roles (already slugged) that credit a person at this
    level. Rows whose role is in it become :class:`Credit`\\ s, in document
    order and with duplicates kept — five "Choreographer" rows are five
    choreographers. Every other row becomes a field, keyed by its printed
    label; a repeated field label keeps the first value, which is what the site
    means by the one case where it happens (two "Notes" rows on a handful of
    productions, the second a continuation the first already reads correctly).
    """
    credits: list[Credit] = []
    fields: dict[str, str] = {}
    for printed, value in pairs(fragment):
        slug = role(printed)
        if slug in known:
            name, note = _split_credit(value)
            if name:
                credits.append(Credit(role=slug, label=printed, name=name, note=note))
            continue
        rendered, extra = _split_credit(value)
        text_value = " ".join(part for part in (rendered, extra) if part)
        if text_value and printed not in fields:
            fields[printed] = text_value
    return tuple(credits), fields


def unknown_labels(fragment: str, known: frozenset[str], fields: frozenset[str]) -> set[str]:
    """Roles in *fragment* that are in neither list — the vocabulary's blind spot.

    Reported by the adapter at the end of a sweep rather than raised, so a new
    label costs one log line and one lost credit instead of a failed run.
    """
    return {slug for printed, _ in pairs(fragment) if (slug := role(printed)) not in known | fields}
