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

Everything else in :data:`~composer_schema.catalogues.CATALOGUE_SCHEMES` is a
composer's catalogue, and only those schemes can hold range or list sets
(#378). Because :data:`~composer_schema.catalogues.CATALOGUE_SCHEMES` is built
from the parser's tables, a catalogue added to :mod:`composer_schema.catalogues`
counts here without a change.
"""

from __future__ import annotations

from composer_schema.catalogues import CATALOGUE_SCHEMES

#: Catalogue schemes numbered afresh for every composer.
PER_COMPOSER_SCHEMES = frozenset({"WoO", "Anh"})


def holds_sets(scheme: str) -> bool:
    """Whether a value in ``scheme`` holding ``-`` or ``,`` is a set of numbers.

    Only a known catalogue's values (:data:`~composer_schema.catalogues.CATALOGUE_SCHEMES`)
    are canonical sets (``1046-1051``); a MusicBrainz id, an ISWC or a Wikidata
    code kept verbatim has hyphens of its own, and reading
    ``4dad1f72-...-799131026480`` as a range would enumerate hundreds of
    billions of members (#378).
    """
    return scheme in CATALOGUE_SCHEMES


def is_composer_scoped(scheme: str) -> bool:
    """Whether a number in ``scheme`` belongs to one composer's catalogue."""
    return scheme in CATALOGUE_SCHEMES and scheme not in PER_COMPOSER_SCHEMES
