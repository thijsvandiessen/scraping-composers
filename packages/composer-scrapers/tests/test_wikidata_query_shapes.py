"""Query-shape guard for every Wikidata adapter (#357 item 2).

The fake WDQS in the adapter tests never runs the query text, so two shapes
that pass them fail live (#359):

- an id query walking ``?item wdt:P31/wdt:P279* ?class`` with ``?item``
  unbound times out with a 504: WDQS starts from every instance of anything.
  Walk the classes first (``?sub wdt:P279* ?class . ?item wdt:P31 ?sub``)
  with the optimizer off.
- a ``|`` alternation path inside a ``UNION`` branch crashes Blazegraph with a
  500 (``PropertyPathUnionNode cannot be cast to JoinGroupNode``). Give each
  alternative a branch of its own.
"""

import re
from types import ModuleType

import pytest
from composer_scrapers.wikidata import catalogue, ensembles, genres, places, query, works

MODULES: tuple[ModuleType, ...] = (query, works, places, genres, ensembles, catalogue)


def _queries() -> dict[str, str]:
    """Every module-level ``*QUERY`` string, as WDQS receives it: a
    ``str.format`` template's doubled braces are single."""
    found: dict[str, str] = {}
    for module in MODULES:
        for name, value in vars(module).items():
            if name.endswith("QUERY") and isinstance(value, str):
                text = value.replace("{{", "{").replace("}}", "}") if "{{" in value else value
                found[f"{module.__name__.rsplit('.', 1)[-1]}.{name}"] = text
    return found


QUERIES = _queries()


def _groups(text: str) -> list[tuple[int, int]]:
    """(start, end) of every ``{...}`` group, braces included."""
    groups: list[tuple[int, int]] = []
    stack: list[int] = []
    for i, char in enumerate(text):
        if char == "{":
            stack.append(i)
        elif char == "}":
            groups.append((stack.pop(), i + 1))
    return groups


def _union_branches(text: str) -> list[str]:
    """The text of every group that is a ``UNION`` operand: followed or
    preceded by the keyword."""
    return [
        text[start:end]
        for start, end in _groups(text)
        if re.match(r"\s*UNION\b", text[end:]) or re.search(r"\bUNION\s*$", text[:start])
    ]


def _alternation(branch: str) -> bool:
    """A ``|`` property path, not the ``||`` operator of a FILTER."""
    return re.search(r"(?<!\|)\|(?!\|)", branch) is not None


def _unbound_class_walk(text: str) -> bool:
    """``?item wdt:P31/wdt:P279*`` without a ``VALUES ?item`` binding it."""
    return "?item wdt:P31/wdt:P279*" in text and not re.search(r"VALUES\s+\?item\b", text)


def test_every_adapter_query_is_collected() -> None:
    for name in (
        "query.ID_QUERY",
        "query.MULTI_QUERY",
        "works.WORK_ID_QUERY",
        "places.PLACE_TYPE_QUERY",
        "genres.NODE_QUERY",
        "ensembles.ENSEMBLE_ID_QUERY",
        "ensembles.ENSEMBLE_MULTI_QUERY",
        "catalogue.CATALOGUE_ID_QUERY",
    ):
        assert name in QUERIES


@pytest.mark.parametrize("name", sorted(QUERIES))
def test_no_unbound_subclass_walk_from_item(name: str) -> None:
    assert not _unbound_class_walk(QUERIES[name])


@pytest.mark.parametrize("name", sorted(QUERIES))
def test_no_alternation_path_inside_a_union_branch(name: str) -> None:
    assert not [branch for branch in _union_branches(QUERIES[name]) if _alternation(branch)]


def test_the_guards_catch_the_shapes_that_failed_live() -> None:
    assert _unbound_class_walk("{ VALUES ?class { wd:Q1 } ?item wdt:P31/wdt:P279* ?class . }")
    assert not _unbound_class_walk("{ VALUES ?item { wd:Q2 } ?item wdt:P31/wdt:P279* ?class . }")
    union = "{ ?item wdt:P1 ?a . } UNION { ?item rdfs:label|skos:altLabel ?name . }"
    assert [b for b in _union_branches(union) if _alternation(b)]
    first = "{ ?item wdt:P1|wdt:P2 ?a . } UNION { ?item wdt:P3 ?b . }"
    assert [b for b in _union_branches(first) if _alternation(b)]
    assert not _alternation("{ FILTER(?a = 1 || ?b = 2) }")
    assert not _union_branches("OPTIONAL { ?item wdt:P297|wdt:P300 ?iso . }")
