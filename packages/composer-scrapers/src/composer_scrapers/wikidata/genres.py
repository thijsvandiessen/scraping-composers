"""The genre and movement hierarchy, from the Wikidata Query Service (#349).

The ``wikidata`` source tags composers with genres (P136) and movements (P135)
by QID. This source supplies what those QIDs are: one record per genre or
movement, with its label, English aliases and ``subclass_of`` claims for its
``subclass of`` (P279) parents. Ingested, those claims make the hierarchy that
lets "dance music" find a composer tagged only with "waltz".

The seeds are every value the composer population uses, asked live on each run
like the composer id list (see :func:`_fetch_seeds`). From them the walk goes
upward, a level at a time, through P279 in VALUES batches, each batch's node
and parent answers mirrored under the query digest exactly as the composer
detail pages are. P279 has cycles, so nothing is visited twice, and the walk
stops after :data:`MAX_DEPTH` levels whatever is left.

Climbing P279 soon leaves music: "waltz" is a subclass of "dance music", which
is a subclass of "music", which is a subclass of "art". A parent only stays if
it is an instance of one of the kind's classes (:data:`CLASSES`, matched
through ``P31/P279*``), and the walk does not climb past one that is not.
Seeds stay whatever their class: a composer was tagged with them. An edge to a
parent that was left out — filtered, unnamed or past the depth cap — is
dropped and counted in the log.

Wikidata often splits one name into two items, linked by ``different from``
(P1889): the ballroom dance "waltz" (Q193434, a type of dance) and the music
genre "waltz" (Q8701407). Composers are tagged with the dance, whose only
parent ("ballroom dance") the class filter drops, so the walk would stop at the
seed. A seed with no parent of the kind's classes gets its *counterpart* as a
parent instead (see :meth:`_Walk._counterparts`): a P1889 item of the kind's classes
with the seed's English label, or that label plus "music" ("New Age" -> "new
age music"). It is walked like any other parent, so the dance reaches "dance
music". Measured on the seeds composers use (#364), ``facet of`` (P1269) and a
second hop through the dropped parent reach no music genre at all; and a P1889
item under another name ("electronic music" -> "electronica") is a different
genre, so it is not followed.

Musical forms ("symphony", "concerto", "song") are another axis than style: on
Wikidata they are subclasses of "composed musical work", not of a genre, so
they reach no top-level genre. A genre that is a direct instance (P31) of one of
:data:`FORM_CLASSES` carries a ``genre_type`` claim of :data:`MUSICAL_FORM`,
which the warehouse's report counts apart from the orphans.

Each kind is walked on its own, so a QID that is both a genre and a movement
(Wikidata files "Baroque music" under both) gets one record per kind, the same
way the composer claims point at one entity per kind.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from composer_http import SourceSession

from .. import EntityDocument, HttpSourceAdapter, RefreshCadence, SourceClaim
from .parse import _BARE_QID, BASE_URL, _literal
from .query import (
    LABEL_LANGUAGES,
    PAGE_SIZE,
    REQUEST_DELAY_S,
    TIMEOUT_S,
    _fetch_qids,
    _qid,
    _run_query,
    _values,
)

log = logging.getLogger(__name__)

#: Levels above the seeds the walk climbs. Real genre chains are short
#: ("Viennese waltz" -> "waltz" -> "dance music" -> ...); the cap is there for
#: the long tail of over-modelled P279 chains, not for cycles, which the visited
#: set already stops.
MAX_DEPTH = 15

#: The property each kind's seeds hang off a composer by.
PROPERTIES: dict[str, str] = {"genre": "P136", "movement": "P135"}

#: The classes (``QID: label``) a non-seed node of each kind must be an
#: instance of, directly or through ``P279*``. Movements are filed under all
#: three: "Classical period" is a music genre, "Romanticism" a cultural
#: movement.
CLASSES: dict[str, dict[str, str]] = {
    "genre": {"Q188451": "music genre"},
    "movement": {
        "Q188451": "music genre",
        "Q968159": "art movement",
        "Q2198855": "cultural movement",
    },
}

#: The classes (``QID: label``) that make a node of each kind a musical form.
#: Matched through P31 alone, since both are metaclasses whose ``P279*``
#: closure reaches far outside music. "song" is a type of composition but not
#: a musical form; "symphony" and "concerto" are both.
FORM_CLASSES: dict[str, dict[str, str]] = {
    "genre": {"Q862597": "musical form", "Q107487333": "type of musical composition"},
    "movement": {},
}

#: The ``genre_type`` value a musical form's record carries.
MUSICAL_FORM = "musical form"

# The seeds: every value the composer population uses for the kind's property.
# Live, never mirrored, like the composer id list -- a value a composer gained
# since the last run has to be picked up.
SEED_QUERY = """\
SELECT DISTINCT ?value WHERE {{ ?item wdt:P106 wd:Q36834 ; wdt:{prop} ?value . }}
"""

# The fallback when SEED_QUERY times out: the same values, a composer batch at
# a time.
SEED_BATCH_QUERY = """\
SELECT DISTINCT ?value WHERE {{
  VALUES ?item {{ {values} }}
  ?item wdt:{prop} ?value .
}}
"""

# One batch of nodes: label, the kind's classes the node is an instance of, the
# form classes it is a direct instance of, and its English aliases, one per row
# through UNION (see MULTI_QUERY in ``query`` for why not OPTIONAL). The bare
# BIND branch gives every bound item one row whatever it has, so a short answer
# means rows went missing in transit and the coverage check can fail it.
NODE_QUERY = """\
SELECT ?item ?itemLabel ?class ?form ?alias
WHERE {{
  VALUES ?item {{ {values} }}
  {{ BIND(true AS ?seen) }}
  UNION {{ VALUES ?class {{ {classes} }} ?item wdt:P31/wdt:P279* ?class . }}
  UNION {{ VALUES ?form {{ {forms} }} ?item wdt:P31 ?form . }}
  UNION {{ ?item skos:altLabel ?alias . FILTER(LANG(?alias) = "en") }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{languages}" . }}
}}
"""

# One batch of nodes' P279 parents. No labels: a parent that is kept gets its
# own label from its own NODE_QUERY row.
PARENT_QUERY = """\
SELECT ?item ?parent
WHERE {{
  VALUES ?item {{ {values} }}
  ?item wdt:P279 ?parent .
}}
"""

# One batch of stranded seeds' ``different from`` (P1889) items: the candidates
# for their counterpart. No labels, as for the parents.
COUNTERPART_QUERY = """\
SELECT ?item ?other
WHERE {{
  VALUES ?item {{ {values} }}
  ?item wdt:P1889 ?other .
}}
"""


@dataclass
class Node:
    """One genre or movement the walk reached."""

    qid: str
    label: str
    depth: int
    classes: set[str] = field(default_factory=set[str])
    forms: set[str] = field(default_factory=set[str])
    aliases: set[str] = field(default_factory=set[str])
    parents: set[str] = field(default_factory=set[str])
    counterparts: set[str] = field(default_factory=set[str])


def _numeric(qids: set[str]) -> list[str]:
    """QIDs in numeric order -- (length, string) is numeric for QIDs."""
    return sorted(qids, key=lambda qid: (len(qid), qid))


def _same_name(seed: str, other: str) -> bool:
    """Whether ``other`` names what ``seed`` does: the same English label, or
    the label followed by "music", ignoring case."""
    seed, other = seed.strip().casefold(), other.strip().casefold()
    return other in (seed, f"{seed} music")


def _uri_qid(binding: dict[str, Any] | None) -> str | None:
    """The QID of a ``uri`` binding; None for a literal or an "unknown value"
    blank node."""
    if not binding or binding.get("type") != "uri":
        return None
    value: str = binding["value"]
    qid = value.rsplit("/", 1)[-1]
    return qid if _BARE_QID.match(qid) else None


def _seed_values(rows: list[dict[str, Any]]) -> set[str]:
    return {qid for row in rows if (qid := _uri_qid(row.get("value"))) is not None}


def _fetch_seeds(session: SourceSession, prop: str) -> list[str]:
    """Every value of ``prop`` on a composer, numerically ordered.

    One query over the occupation index usually answers in seconds; should it
    time out, the same values are asked a composer batch at a time instead."""
    try:
        rows = _run_query(session, SEED_QUERY.format(prop=prop), f"{prop} values")
        return _numeric(_seed_values(rows))
    except (httpx.HTTPError, ValueError, KeyError):
        log.warning("%s value list failed; asking per composer batch instead", prop, exc_info=True)
    composers = _fetch_qids(session)
    seeds: set[str] = set()
    for i in range(0, len(composers), PAGE_SIZE):
        batch = composers[i : i + PAGE_SIZE]
        query = SEED_BATCH_QUERY.format(values=_values(batch), prop=prop)
        seeds |= _seed_values(_run_query(session, query, f"{prop} values for {len(batch)} composers"))
    return _numeric(seeds)


def _fetch_nodes(
    session: SourceSession, qids: list[str], classes: dict[str, str], forms: dict[str, str]
) -> dict[str, dict[str, Any]]:
    """Label, matched classes, form classes and aliases for one batch, keyed
    by QID.

    Every bound item must come back (see NODE_QUERY); a short answer fails the
    run and is never mirrored, as for the composer detail pages."""
    query = NODE_QUERY.format(
        values=_values(qids),
        classes=_values(sorted(classes)),
        forms=_values(sorted(forms)),
        languages=LABEL_LANGUAGES,
    )

    def covered(rows: list[dict[str, Any]]) -> bool:
        return not set(qids) - {_qid(row) for row in rows}

    rows = _run_query(session, query, f"nodes for {len(qids)} items", mirror=True, complete=covered)
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        node = found.setdefault(_qid(row), {"label": "", "classes": set(), "forms": set(), "aliases": set()})
        node["label"] = node["label"] or _literal(row, "itemLabel") or ""
        if (cls := _uri_qid(row.get("class"))) is not None:
            node["classes"].add(cls)
        if (form := _uri_qid(row.get("form"))) is not None:
            node["forms"].add(form)
        if (alias := _literal(row, "alias")) is not None:
            node["aliases"].add(alias)
    missing = set(qids) - found.keys()
    if missing:
        raise RuntimeError(
            f"wikidata node batch returned {len(qids) - len(missing)} of {len(qids)} requested items; "
            f"missing e.g. {sorted(missing)[:5]}"
        )
    return found


def _fetch_edges(session: SourceSession, template: str, var: str, qids: list[str]) -> dict[str, set[str]]:
    """The ``var`` items of one batch through ``template`` (PARENT_QUERY or
    COUNTERPART_QUERY), keyed by QID. An item with none has no row, so there is
    nothing to check coverage against."""
    query = template.format(values=_values(qids))
    edges: dict[str, set[str]] = {}
    for row in _run_query(session, query, f"{var}s of {len(qids)} items", mirror=True):
        if (target := _uri_qid(row.get(var))) is not None:
            edges.setdefault(_qid(row), set()).add(target)
    return edges


def _fetch_parents(session: SourceSession, qids: list[str]) -> dict[str, set[str]]:
    """The P279 parents of one batch, keyed by QID."""
    return _fetch_edges(session, PARENT_QUERY, "parent", qids)


def _fetch_counterparts(session: SourceSession, qids: list[str]) -> dict[str, set[str]]:
    """The P1889 items of one batch, keyed by QID."""
    return _fetch_edges(session, COUNTERPART_QUERY, "other", qids)


class _Walk:
    """The upward P279 walk, with the ``max_pages`` batch budget it shares
    across kinds."""

    def __init__(self, session: SourceSession, max_pages: int | None) -> None:
        self.session = session
        self.remaining = max_pages

    def _take_batch(self) -> bool:
        if self.remaining is None:
            return True
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True

    def nodes(self, kind: str, seeds: list[str]) -> list[Node]:
        """Every node of ``kind`` the walk keeps, parents trimmed to kept nodes."""
        kept: dict[str, Node] = {}
        visited = set(seeds)
        frontier = seeds
        depth = 0
        # Depth 1 runs even with nothing to climb to: that is where the
        # stranded seeds get their counterparts.
        while frontier or depth == 1:
            level = self._level(kind, frontier, depth)
            kept.update((node.qid, node) for node in level)
            if depth == 1:
                level.extend(self._counterparts(kind, kept, visited))
                kept.update((node.qid, node) for node in level)
            above = {parent for node in level for parent in node.parents} - visited
            if above and depth == MAX_DEPTH:
                log.warning("%s walk: depth cap %d reached, %d parents left", kind, MAX_DEPTH, len(above))
                break
            visited |= above
            frontier = _numeric(above)
            depth += 1

        dropped = 0
        for node in kept.values():
            dropped += len(node.parents - kept.keys())
            node.parents &= kept.keys()
            node.counterparts &= kept.keys()
        log.info("%s walk: %d nodes, %d edges to dropped parents", kind, len(kept), dropped)
        return sorted(kept.values(), key=lambda node: (node.depth, len(node.qid), node.qid))

    def _level(self, kind: str, frontier: list[str], depth: int) -> list[Node]:
        """The kept nodes of one level, a batch at a time while the budget lasts."""
        level: list[Node] = []
        for i in range(0, len(frontier), PAGE_SIZE):
            if not self._take_batch():
                log.info("%s walk: stopping at depth %d after max_pages", kind, depth)
                break
            level.extend(self._batch(kind, frontier[i : i + PAGE_SIZE], depth))
        return level

    def _counterparts(self, kind: str, kept: dict[str, Node], visited: set[str]) -> list[Node]:
        """Link every stranded seed to its counterpart, if it has one, and
        return the counterparts new to the walk as depth-1 nodes, so their own
        parents are climbed next.

        Runs once the seeds' parents have been through the class filter. A seed
        is stranded when none of its parents is of the kind's classes: all were
        filtered out, or the only ones kept are seeds outside the classes too
        ("ballet" the dance under "performing arts", which a composer is also
        tagged with). A counterpart must be of the kind's classes. One already
        kept (a seed, or a parent of another seed) is linked as it is, and one
        visited but not kept was filtered out. The rest are fetched as a
        depth-1 level; one whose label matches no seed that named it stays
        unvisited, so a P279 path can still reach it later."""
        if self.remaining is not None and self.remaining <= 0:
            return []  # max_pages is spent: no batch left to fetch them with

        def of_kind(qid: str) -> bool:
            return qid in kept and bool(kept[qid].classes)

        stranded = [
            node
            for node in kept.values()
            if node.depth == 0 and not any(of_kind(parent) for parent in node.parents - {node.qid})
        ]
        candidates: dict[str, set[str]] = {}
        for i in range(0, len(stranded), PAGE_SIZE):
            candidates |= _fetch_counterparts(
                self.session, [node.qid for node in stranded[i : i + PAGE_SIZE]]
            )
        wanted = {other for others in candidates.values() for other in others} - visited
        fetched = {node.qid: node for node in self._level(kind, _numeric(wanted), 1)}
        new: dict[str, Node] = {}
        for seed in stranded:
            for qid in _numeric(candidates.get(seed.qid, set()) - {seed.qid}):
                other = kept.get(qid) or fetched.get(qid)
                if other is None or not other.classes or not _same_name(seed.label, other.label):
                    continue
                seed.parents.add(qid)
                seed.counterparts.add(qid)
                if qid in fetched:
                    new[qid] = other
        visited |= new.keys()
        linked = sum(1 for seed in stranded if seed.counterparts)
        log.info("%s walk: %d stranded seeds, %d given a counterpart", kind, len(stranded), linked)
        return list(new.values())

    def _batch(self, kind: str, qids: list[str], depth: int) -> list[Node]:
        nodes: list[Node] = []
        for qid, found in sorted(_fetch_nodes(self.session, qids, CLASSES[kind], FORM_CLASSES[kind]).items()):
            label: str = found["label"]
            if not label or _BARE_QID.match(label):
                log.debug("%s %s: no label, dropped", kind, qid)
                continue
            if depth > 0 and not found["classes"]:
                log.debug("%s %s (%s): not of the kind's classes, dropped", kind, qid, label)
                continue
            nodes.append(Node(qid, label, depth, found["classes"], found["forms"], found["aliases"]))
        if nodes:
            parents = _fetch_parents(self.session, [node.qid for node in nodes])
            for node in nodes:
                node.parents = parents.get(node.qid, set())
        return nodes


class WikidataGenresAdapter(HttpSourceAdapter[EntityDocument]):
    name = "wikidata_genres"
    base_url = BASE_URL
    cadence = RefreshCadence.YEARLY
    request_delay_s = REQUEST_DELAY_S
    timeout_s = TIMEOUT_S

    def scrape(self, session: SourceSession, max_pages: int | None = None) -> Iterator[EntityDocument]:
        """Yield every genre and movement the composers use, and their
        ancestors. ``max_pages`` caps the node batches across both kinds, for
        test runs; a capped walk keeps what it reached, nearest levels first."""
        walk = _Walk(session, max_pages)
        for kind, prop in PROPERTIES.items():
            seeds = _fetch_seeds(session, prop)
            if not seeds:
                raise RuntimeError(f"wikidata returned no composer {kind} values ({prop})")
            log.info("wikidata_genres: %d %s seeds", len(seeds), kind)
            nodes = walk.nodes(kind, seeds)
            labels = {node.qid: node.label for node in nodes}
            ingested_at = datetime.now(UTC)
            for node in nodes:
                yield self._document(kind, node, labels, ingested_at)

    def _document(
        self, kind: str, node: Node, labels: dict[str, str], ingested_at: datetime
    ) -> EntityDocument:
        parents = _numeric(node.parents)
        claims = [
            SourceClaim("subclass_of", kind, labels[parent], object_wikidata_id=parent) for parent in parents
        ]
        claims.extend(SourceClaim("also_known_as", value=alias) for alias in sorted(node.aliases))
        if node.forms:
            claims.append(SourceClaim("genre_type", value=MUSICAL_FORM))
        return EntityDocument(
            id=f"{kind}:{node.qid}",
            url=f"{BASE_URL}/wiki/{node.qid}",
            source_name=self.name,
            ingested_at=ingested_at,
            name=node.label,
            kind=kind,
            raw={
                "item": node.qid,
                "label": node.label,
                "classes": sorted(node.classes),
                "forms": _numeric(node.forms),
                "parents": parents,
                "counterparts": _numeric(node.counterparts),
                "depth": node.depth,
            },
            claims=tuple(claims),
        )
