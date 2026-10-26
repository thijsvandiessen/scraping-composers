"""Which identifier schemes name one composer's works.

A Köchel number is Mozart's and a BWV number Bach's, so two composer entities
holding the same ``KV 550`` are evidence of one composer -- what both the work
merge (:mod:`.merge`) and the person dedupe
(:mod:`composer_warehouse.persons.catalogues`) lean on (#362). Not every scheme
says that much:

- ``WoO`` and ``Anh`` are numbered per composer, so Beethoven's WoO 59 and
  Brahms's WoO 59 are two works;
- an opus number is the publisher's, and reassigned;
- an external id (Wikidata, MusicBrainz, ISWC) names a work, but an
  arrangement is often filed under the arranger;
- a Wikidata catalogue kept by its item (``wd:Q...``) may be any catalogue at
  all, a publisher's or a library's among them.

Everything else is a composer's catalogue. The rule is written as the
exceptions so a catalogue added to :mod:`composer_schema.catalogues` counts
without a change here.
"""

from __future__ import annotations

from composer_schema.catalogues import OPUS, WIKIDATA_SCHEME_PREFIX

from .match import EXTERNAL_SCHEMES

#: Schemes numbered afresh for every composer.
PER_COMPOSER_SCHEMES = frozenset({"WoO", "Anh", OPUS})


def holds_sets(scheme: str) -> bool:
    """Whether a value in ``scheme`` holding ``-`` or ``,`` is a set of numbers.

    Only a catalogue's values are canonical sets (``1046-1051``); a
    MusicBrainz id, an ISWC or a Wikidata code kept verbatim has hyphens of
    its own, and reading ``4dad1f72-...-799131026480`` as a range would
    enumerate hundreds of billions of members.
    """
    return not (scheme in EXTERNAL_SCHEMES or scheme.startswith(WIKIDATA_SCHEME_PREFIX))


def is_composer_scoped(scheme: str) -> bool:
    """Whether a number in ``scheme`` belongs to one composer's catalogue."""
    return not (
        scheme in PER_COMPOSER_SCHEMES
        or scheme in EXTERNAL_SCHEMES
        or scheme.startswith(WIKIDATA_SCHEME_PREFIX)
    )
