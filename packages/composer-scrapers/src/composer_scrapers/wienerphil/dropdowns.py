"""The archive's filter dropdowns: the source's own vocabularies.

The landing page ships every filter's options inline, one ``<ul filter-list>``
per filter, and they are exhaustive: the ``komponist`` and ``interpret`` lists
between them name every composer and every performer that appears anywhere in
the archive's concerts, spelled exactly as the concerts spell them. So they play
two parts here — ``werk`` disambiguates the programme split (see
:mod:`.performances`), and the other two are the person list, one record each,
the way :mod:`composer_scrapers.concertgebouw.dropdowns` uses that archive's
``<select>`` options.

Only the concerts say what anyone *did*, though: the archive files conductors
and soloists together under one "Interpret" heading. A performer is therefore
credited as a conductor when the concerts ever credited them as one, and as a
soloist otherwise, which the adapter decides once it has read them all.
"""

from __future__ import annotations

import html
import re
from collections.abc import Container

from composer_schema.kinds import ENSEMBLE_KIND, PERSON_KIND, looks_like_ensemble

from .. import SourceClaim, SourceRecord

#: Filter list name -> the vocabulary it holds.
COMPOSERS = "komponist"
WORKS = "werk"
PERFORMERS = "interpret"
VENUES = "spielort"

# <ul filter-list="komponist" class="filter-result-list-ul"> ... </ul>
_LIST = re.compile(r'<ul filter-list="([a-z]+)"[^>]*>(.*?)</ul>', re.DOTALL)
# <a href="javascript:;" class="filter-result-list-a" title="Johann Joseph Abert">
_OPTION = re.compile(r'class="filter-result-list-a" title="([^"]*)"')


def vocabularies(landing: str) -> dict[str, list[str]]:
    """Every filter vocabulary of the archive page, keyed by filter name.

    A filter is rendered twice (desktop and mobile); the first copy wins, both
    being the same list.
    """
    found: dict[str, list[str]] = {}
    for name, body in _LIST.findall(landing):
        if name in found:
            continue
        options = [html.unescape(option).strip() for option in _OPTION.findall(body)]
        if options:
            found[name] = options
    return found


def composer_record(name: str) -> SourceRecord:
    """A composer, asserted as one by the archive listing them under Composer."""
    return SourceRecord(
        external_id=f"{COMPOSERS}:{name}",
        name=name,
        url=None,
        raw={"filter": COMPOSERS, "label": name},
        claims=(SourceClaim("has_profession", "profession", "composer"),),
    )


def performer_record(name: str, conductors: Container[str]) -> SourceRecord:
    """A performer, as a conductor if the concerts ever credited them as one.

    Ensembles share the Interpret list with the musicians and are recorded as
    such — an orchestra or a choir has no profession to claim, and letting one
    through as a person would put it into the person dedupe pass.
    """
    if looks_like_ensemble(name):
        return SourceRecord(
            external_id=f"{PERFORMERS}:{name}",
            name=name,
            url=None,
            raw={"filter": PERFORMERS, "label": name},
            kind=ENSEMBLE_KIND,
        )
    profession = "conductor" if name in conductors else "soloist"
    return SourceRecord(
        external_id=f"{PERFORMERS}:{name}",
        name=name,
        url=None,
        raw={"filter": PERFORMERS, "label": name, "profession": profession},
        kind=PERSON_KIND,
        claims=(SourceClaim("has_profession", "profession", profession),),
    )
