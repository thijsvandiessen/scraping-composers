"""Tests for the genre/movement hierarchy walk over a fake WDQS."""

import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from composer_http import PageCache
from composer_http.testing import mock_session
from composer_schema import EntityDocument, SourceClaim
from composer_scrapers import REGISTRY
from composer_scrapers.wikidata import WikidataGenresAdapter
from composer_scrapers.wikidata import genres as genres_mod
from composer_scrapers.wikidata.genres import COUNTERPART_QUERY, NODE_QUERY, PARENT_QUERY, SEED_QUERY

ENTITY = "http://www.wikidata.org/entity/"
MUSIC_GENRE = "Q188451"
ART_MOVEMENT = "Q968159"


@dataclass
class Item:
    label: str
    classes: set[str] = field(default_factory=lambda: {MUSIC_GENRE})
    parents: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    forms: set[str] = field(default_factory=set[str])
    different: tuple[str, ...] = ()


@dataclass
class FakeWdqs:
    """WDQS over a little P279 graph. ``seeds`` maps a property to the values
    composers use; ``asked`` records every query in order."""

    items: dict[str, Item]
    seeds: dict[str, list[str]]
    seed_status: int = 200
    asked: list[str] = field(default_factory=list[str])

    def answer(self, query: str) -> list[dict[str, Any]]:
        items = _values(query, "item")
        if "?seen" in query:
            wanted = set(_values(query, "class"))
            rows: list[dict[str, Any]] = []
            for qid in items:
                item = self.items[qid]
                rows.append({**_item(qid), "itemLabel": _lit(item.label)})
                rows += [{**_item(qid), "class": _uri(c)} for c in sorted(item.classes & wanted)]
                rows += [
                    {**_item(qid), "form": _uri(f)} for f in sorted(item.forms & set(_values(query, "form")))
                ]
                rows += [{**_item(qid), "alias": _lit(a)} for a in item.aliases]
            return rows
        if "wdt:P279 ?parent" in query:
            return [{**_item(q), "parent": _uri(p)} for q in items for p in self.items[q].parents]
        if "wdt:P1889 ?other" in query:
            return [{**_item(q), "other": _uri(o)} for q in items for o in self.items[q].different]
        prop = re.search(r"wdt:(P13[56]) \?value", query)
        if prop is not None:
            return [{"value": _uri(v)} for v in self.seeds.get(prop.group(1), [])]
        return [_item(f"Q{n}") for n in range(900, 905)]  # the composer id list

    def handler(self, request: httpx.Request) -> httpx.Response:
        query = urllib.parse.parse_qs(request.read().decode())["query"][0]
        self.asked.append(query)
        if "?value" in query and "VALUES" not in query and self.seed_status != 200:
            return httpx.Response(self.seed_status)
        return httpx.Response(200, json={"results": {"bindings": self.answer(query)}})


def _values(query: str, var: str) -> list[str]:
    block = re.search(rf"VALUES \?{var} \{{([^}}]*)\}}", query)
    return re.findall(r"wd:(Q\d+)", block.group(1)) if block else []


def _item(qid: str) -> dict[str, Any]:
    return {"item": _uri(qid)}


def _uri(qid: str) -> dict[str, str]:
    return {"type": "uri", "value": f"{ENTITY}{qid}"}


def _lit(value: str) -> dict[str, str]:
    return {"type": "literal", "value": value}


def _fetch(
    monkeypatch: pytest.MonkeyPatch, wdqs: FakeWdqs, cache: PageCache | None = None, **kwargs: Any
) -> dict[str, EntityDocument]:
    monkeypatch.setattr(
        WikidataGenresAdapter, "open_session", lambda self: mock_session(wdqs.handler, cache=cache)
    )
    return {doc.id: doc for doc in WikidataGenresAdapter().fetch(**kwargs)}


def _parents(doc: EntityDocument) -> list[str | None]:
    return [c.object_wikidata_id for c in doc.claims if c.predicate == "subclass_of"]


# Viennese waltz -> waltz -> dance music -> music (not a music genre)
GRAPH = {
    "Q1": Item("Viennese waltz", parents=("Q2",)),
    "Q2": Item("waltz", parents=("Q3",), aliases=("valse",)),
    "Q3": Item("dance music", parents=("Q4",)),
    "Q4": Item("music", classes=set(), parents=("Q5",)),
    "Q5": Item("art", classes=set()),
    "Q10": Item("Baroque music", classes={MUSIC_GENRE, ART_MOVEMENT}, parents=("Q11",)),
    "Q11": Item("early music", classes={ART_MOVEMENT}),
}
SEEDS = {"P136": ["Q1", "Q2"], "P135": ["Q10"]}


def test_seeds_and_their_ancestors_become_records_with_subclass_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = _fetch(monkeypatch, FakeWdqs(GRAPH, SEEDS))

    assert set(docs) == {"genre:Q1", "genre:Q2", "genre:Q3", "movement:Q10", "movement:Q11"}
    waltz = docs["genre:Q2"]
    assert waltz.kind == "genre"
    assert waltz.name == "waltz"
    assert waltz.url == "https://www.wikidata.org/wiki/Q2"
    assert waltz.claims == (
        SourceClaim("subclass_of", "genre", "dance music", object_wikidata_id="Q3"),
        SourceClaim("also_known_as", value="valse"),
    )
    assert waltz.raw == {
        "item": "Q2",
        "label": "waltz",
        "classes": [MUSIC_GENRE],
        "forms": [],
        "parents": ["Q3"],
        "counterparts": [],
        "depth": 0,
    }
    assert _parents(docs["genre:Q1"]) == ["Q2"]
    assert docs["genre:Q3"].raw["depth"] == 1
    assert _parents(docs["movement:Q10"]) == ["Q11"]
    assert docs["movement:Q11"].kind == "movement"


def test_a_parent_outside_the_kinds_classes_is_dropped_and_not_climbed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wdqs = FakeWdqs(GRAPH, SEEDS)
    docs = _fetch(monkeypatch, wdqs)

    assert "genre:Q4" not in docs
    assert _parents(docs["genre:Q3"]) == []  # the edge to "music" went with it
    # "art" is music's parent: the walk never asked about it
    assert not any("Q5" in _values(query, "item") for query in wdqs.asked)


def test_a_seed_outside_the_classes_is_kept() -> None:
    """A composer was tagged with it, so it is the vocabulary whatever its class."""
    wdqs = FakeWdqs({"Q1": Item("sardana", classes=set())}, {"P136": ["Q1"], "P135": ["Q1"]})
    with mock_session(wdqs.handler) as session:
        nodes = genres_mod._Walk(session, None).nodes("genre", ["Q1"])
    assert [node.label for node in nodes] == ["sardana"]


# The ballroom dance "waltz" (a type of dance, not a music genre) whose only
# parent the class filter drops, and its P1889 items: the music genre of the
# same name, and a genre with another name.
DANCE_GRAPH = GRAPH | {
    "Q20": Item("waltz", classes=set(), parents=("Q21",), different=("Q22", "Q23")),
    "Q21": Item("ballroom dance", classes=set()),
    "Q22": Item("Waltz", parents=("Q3",)),
    "Q23": Item("polka", parents=("Q3",)),
    "Q24": Item("New Age", classes=set(), different=("Q25",)),
    "Q25": Item("new age music"),
    "Q26": Item("ballet", classes=set(), different=("Q27",)),
    "Q27": Item("ballet", classes=set()),  # the same name, but not a music genre
}


def test_a_stranded_seed_climbs_through_its_same_named_counterpart(monkeypatch: pytest.MonkeyPatch) -> None:
    docs = _fetch(monkeypatch, FakeWdqs(DANCE_GRAPH, {"P136": ["Q20"], "P135": ["Q10"]}))

    assert _parents(docs["genre:Q20"]) == ["Q22"]
    assert docs["genre:Q20"].raw["counterparts"] == ["Q22"]
    assert docs["genre:Q22"].raw["depth"] == 1
    assert _parents(docs["genre:Q22"]) == ["Q3"]  # and on up to dance music
    assert "genre:Q21" not in docs  # the dance's own parent is still dropped
    assert "genre:Q23" not in docs  # another name is another genre


def test_a_counterpart_may_add_music_to_the_name_but_must_be_of_the_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every seed here has no parent at all, so depth 1 has nothing to climb
    to and runs for the counterparts alone."""
    docs = _fetch(monkeypatch, FakeWdqs(DANCE_GRAPH, {"P136": ["Q24", "Q26"], "P135": ["Q10"]}))

    assert _parents(docs["genre:Q24"]) == ["Q25"]
    assert _parents(docs["genre:Q26"]) == []
    assert "genre:Q27" not in docs


def test_a_counterpart_that_is_already_kept_is_linked_without_a_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wdqs = FakeWdqs(DANCE_GRAPH, {"P136": ["Q20", "Q22"], "P135": ["Q10"]})
    docs = _fetch(monkeypatch, wdqs)

    assert _parents(docs["genre:Q20"]) == ["Q22"]
    assert docs["genre:Q22"].raw["depth"] == 0
    assert sum("?seen" in q and "Q22" in _values(q, "item") for q in wdqs.asked) == 1


def test_a_seed_whose_only_kept_parent_is_a_classless_seed_is_stranded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "ballet" the dance sits under "performing arts", which a composer is
    tagged with too: kept, but not a music genre, so it leads nowhere."""
    graph = DANCE_GRAPH | {
        "Q26": Item("ballet", classes=set(), parents=("Q40",), different=("Q41",)),
        "Q40": Item("performing arts", classes=set()),
        "Q41": Item("ballet", parents=("Q3",)),
    }
    docs = _fetch(monkeypatch, FakeWdqs(graph, {"P136": ["Q26", "Q40"], "P135": ["Q10"]}))

    assert _parents(docs["genre:Q26"]) == ["Q40", "Q41"]
    assert docs["genre:Q26"].raw["counterparts"] == ["Q41"]


def test_only_a_seed_with_no_kept_parent_asks_for_counterparts(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = DANCE_GRAPH | {"Q2": Item("waltz", parents=("Q3",), different=("Q22",))}
    wdqs = FakeWdqs(graph, {"P136": ["Q2", "Q20"], "P135": ["Q10"]})
    docs = _fetch(monkeypatch, wdqs)

    assert _parents(docs["genre:Q2"]) == ["Q3"]
    asked = [_values(q, "item") for q in wdqs.asked if "wdt:P1889" in q]
    assert asked == [["Q20"]]


def test_a_musical_form_carries_a_genre_type_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = GRAPH | {
        "Q30": Item("symphony", forms={"Q862597"}, parents=("Q31",)),
        "Q31": Item("composed musical work", classes=set()),
    }
    docs = _fetch(monkeypatch, FakeWdqs(graph, {"P136": ["Q30", "Q2"], "P135": ["Q10"]}))

    assert SourceClaim("genre_type", value="musical form") in docs["genre:Q30"].claims
    assert docs["genre:Q30"].raw["forms"] == ["Q862597"]
    assert not any(c.predicate == "genre_type" for c in docs["genre:Q2"].claims)


def test_cycles_terminate_and_keep_both_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = {"Q1": Item("a", parents=("Q2",)), "Q2": Item("b", parents=("Q1",)), "Q9": Item("m")}
    wdqs = FakeWdqs(graph, {"P136": ["Q1"], "P135": ["Q9"]})
    docs = _fetch(monkeypatch, wdqs)

    assert _parents(docs["genre:Q1"]) == ["Q2"]
    assert _parents(docs["genre:Q2"]) == ["Q1"]
    assert sum("?seen" in query and "Q1" in _values(query, "item") for query in wdqs.asked) == 1


def test_the_walk_stops_at_the_depth_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    chain = {f"Q{n}": Item(f"g{n}", parents=(f"Q{n + 1}",)) for n in range(1, 6)} | {"Q6": Item("g6")}
    monkeypatch.setattr(genres_mod, "MAX_DEPTH", 2)
    docs = _fetch(monkeypatch, FakeWdqs(chain | {"Q9": Item("m")}, {"P136": ["Q1"], "P135": ["Q9"]}))

    assert {doc_id for doc_id in docs if doc_id.startswith("genre:")} == {"genre:Q1", "genre:Q2", "genre:Q3"}
    assert _parents(docs["genre:Q3"]) == []  # Q4 was past the cap


def test_batches_cover_every_seed_across_page_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = {f"Q{n}": Item(f"g{n}") for n in range(1, 8)}
    monkeypatch.setattr(genres_mod, "PAGE_SIZE", 2)
    wdqs = FakeWdqs(graph, {"P136": [f"Q{n}" for n in range(1, 7)], "P135": ["Q7"]})
    docs = _fetch(monkeypatch, wdqs)

    assert len(docs) == 7
    assert max(len(_values(q, "item")) for q in wdqs.asked if "?seen" in q) == 2


def test_max_pages_caps_the_node_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(genres_mod, "PAGE_SIZE", 1)
    docs = _fetch(monkeypatch, FakeWdqs(GRAPH, SEEDS), max_pages=2)

    # two batches: the two genre seeds; their parents and the movements are cut
    assert set(docs) == {"genre:Q1", "genre:Q2"}
    assert _parents(docs["genre:Q2"]) == []


def test_a_node_batch_missing_an_item_fails_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    wdqs = FakeWdqs(GRAPH, SEEDS)
    original = wdqs.answer
    monkeypatch.setattr(wdqs, "answer", lambda q: [r for r in original(q) if r.get("item") != _uri("Q2")])
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="1 of 2"):
        _fetch(monkeypatch, wdqs)


def test_unlabelled_nodes_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = GRAPH | {"Q3": Item("Q3", parents=("Q4",))}
    docs = _fetch(monkeypatch, FakeWdqs(graph, SEEDS))
    assert "genre:Q3" not in docs
    assert _parents(docs["genre:Q2"]) == []


def test_no_seeds_is_an_error_not_an_empty_vocabulary(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="no composer genre values"):
        _fetch(monkeypatch, FakeWdqs(GRAPH, {"P135": ["Q10"]}))


def test_a_failing_seed_query_falls_back_to_composer_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)
    wdqs = FakeWdqs(GRAPH, SEEDS, seed_status=504)

    def answer(query: str) -> list[dict[str, Any]]:
        if "VALUES ?item" in query and "?value" in query:
            prop = "P136" if "P136" in query else "P135"
            return [{"value": _uri(v)} for v in SEEDS[prop]]
        return FakeWdqs.answer(wdqs, query)

    monkeypatch.setattr(wdqs, "answer", answer)
    docs = _fetch(monkeypatch, wdqs)
    assert "genre:Q1" in docs
    assert any("VALUES ?item" in q and "wdt:P136 ?value" in q for q in wdqs.asked)


def test_node_and_parent_batches_are_mirrored_and_seeds_are_not(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = PageCache(tmp_path / "pages.db")
    wdqs = FakeWdqs(GRAPH, SEEDS)
    first = _fetch(monkeypatch, wdqs, cache)
    asked_first = len(wdqs.asked)
    second = _fetch(monkeypatch, wdqs, cache)

    assert second.keys() == first.keys()
    again = wdqs.asked[asked_first:]
    assert len(again) == 2  # the two live seed queries
    assert all("?value" in query for query in again)


def test_queries_ask_what_the_walk_reads() -> None:
    assert "wdt:P31/wdt:P279*" in NODE_QUERY
    assert 'FILTER(LANG(?alias) = "en")' in NODE_QUERY
    assert "wdt:P279 ?parent" in PARENT_QUERY
    assert "wdt:P1889 ?other" in COUNTERPART_QUERY
    assert "?item wdt:P31 ?form" in NODE_QUERY
    assert "wdt:P106 wd:Q36834" in SEED_QUERY


def test_registered() -> None:
    adapter = REGISTRY["wikidata_genres"]
    assert isinstance(adapter, WikidataGenresAdapter)
    assert adapter.name == "wikidata_genres"
