"""Tests for the documents the Classicapedia adapter yields, over a fake site."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator

import pytest
from composer_schema import EntityDocument, WorkMentionDocument
from composer_scrapers import REGISTRY
from composer_scrapers.classicapedia import ClassicapediaAdapter, classify_kind, name_key, shared_names
from composer_scrapers.classicapedia.fetch import IndexEntry, PageRef
from composer_scrapers.classicapedia.resolve import WikidataItem

Doc = EntityDocument | WorkMentionDocument


def _page(name: str, subtitle: str, relations: str = "", works: str = "", cited: str = "") -> str:
    citation = (
        f'<p class="fuente-wiki"><a href="https://es.wikipedia.org/wiki/{cited}">w</a></p>' if cited else ""
    )
    return (
        f'<article class="ficha"><h1>{name}</h1><p class="sub">{subtitle}</p>'
        f'<h2 id="obras">Works</h2><ul class="obras">{works}</ul>'
        f'<section id="maestros">{relations}</section>{citation}</article>'
    )


def _list(heading: str, *items: str) -> str:
    return f'<h3>{heading}</h3><ul class="obras rel">{"".join(f"<li>{item}</li>" for item in items)}</ul>'


def _profile(tag: str, name: str, article: str | None) -> str:
    reading = (
        f'<p class="mas-info"><a href="https://en.wikipedia.org/wiki/{article}">w</a></p>' if article else ""
    )
    return (
        f'<article class="ficha"><p class="periodo-tag tipo-tag">{tag}</p><h1>{name}</h1>'
        '<dl class="datos-sala"><dt>Opened</dt><dd>1778</dd><dt>Founded</dt><dd>1842</dd></dl>'
        f"{reading}</article>"
    )


PAGES = {
    ("b", "haydn"): _page(
        "Joseph Haydn",
        "1732–1809 · Rohrau · Austrian composer",
        _list(
            "Pupils",
            '<a href="beethoven.html">Beethoven link text</a> <span class="mat">(counterpoint)</span>',
            '<a href="strauss-ii.html">Johann Strauss</a>',
            '<a href="strauss-pinned.html">Johann Strauss</a>',
            '<a href="haydn.html">Joseph Haydn</a>',
            '<a href="beethoven.html">Beethoven link text</a>',
            '<a href="suk.html">Josef Suk</a>',
            '<a href="suk-son.html">Josef Suk</a>',
            "Czerny Unlinked",
            "Held",
            "Johann Strauss",
            "Joseph Haydn",
        ),
        '<li>Symphony no.94, G <span class="anyo">(1791)</span></li>',
        cited="Joseph_Haydn",
    ),
    ("b", "beethoven"): _page("Ludwig van Beethoven", "1770–1827 · Bonn · composer", cited="Fidelio"),
    ("b", "strauss-ii"): _page("Johann Strauss", "1825–1899 · Vienna · composer", works="<li>A waltz</li>"),
    ("b", "strauss-pinned"): _page(
        "Johann Strauss", "1804–1849 · Vienna · composer", works="<li>A march</li>", cited="Johann_Strauss_I"
    ),
    ("b", "suk"): _page("Josef Suk", "1874–1935 · Křečovice · composer", works="<li>Asrael</li>"),
    ("b", "suk-son"): _page("Josef Suk", "b. 1929 · Prague · violinist"),
    ("b", "many-a"): _page("Giovanni Rossi", "1700–1760 · Rome · composer"),
    ("b", "many-b"): _page("Giovanni Rossi", "1800–1860 · Parma · composer"),
    ("b", "twin-a"): _page("Twin Page", "1600–1660 · Rome · composer"),
    ("b", "twin-b"): _page("Twin Page", "1600–1660 · Rome · composer"),
    ("b", "pinned-a"): _page("Cited Twice", "1500–1560 · Rome · composer", cited="Cited_Twice"),
    ("b", "pinned-b"): _page("Cited Twice", "1500–1560 · Rome · composer"),
    ("b", "haas"): _page("Haas family", "1689–1752 · Hamburg · family of harpsichord makers"),
    ("b", "czerny"): _page("Czerny Unlinked", "1791–1857 · Vienna · pianist and teacher"),
    ("t", "scala"): _profile("Opera house · Milan, Italy", "La Scala", "La_Scala"),
    ("t", "wno"): _profile(
        "Opera house · Cardiff, United Kingdom", "Welsh National Opera", "Welsh_National_Opera"
    ),
    ("s", "hall"): _profile("Concert hall · Nowhere, Atlantis", "Lost Hall", None),
    ("o", "wien"): _profile("Orchestra · Vienna, Austria", "Vienna Philharmonic", "Vienna_Philharmonic"),
}

ENTRIES = [
    IndexEntry("haydn", "Joseph Haydn", ("compositor", "pedagogo"), "Austrian"),
    IndexEntry("beethoven", "Ludwig van Beethoven", ("compositor",), "German"),
    IndexEntry("strauss-ii", "Johann Strauss", ("compositor",), "Austrian"),
    IndexEntry("strauss-pinned", "Johann Strauss", ("compositor", "batuta"), "Austrian"),
    IndexEntry("suk", "Josef Suk", ("compositor",), "Czech"),
    IndexEntry("suk-son", "Josef Suk", (), "Czech"),
    IndexEntry("many-a", "Giovanni Rossi", (), "Italian"),
    IndexEntry("many-b", "Giovanni Rossi", (), "Italian"),
    IndexEntry("twin-a", "Twin Page", (), "Italian"),
    IndexEntry("twin-b", "Twin Page", (), "Italian"),
    IndexEntry("pinned-a", "Cited Twice", (), "Italian"),
    IndexEntry("pinned-b", "Cited Twice", (), "Italian"),
    IndexEntry("haas", "Haas family", ("constructor",), "German"),
    IndexEntry("czerny", "Czerny Unlinked", ("teclado",), "Austrian"),
]

QIDS = {
    ("es", "Joseph Haydn"): "Q7349",
    ("es", "Fidelio"): "Q1",
    ("es", "Johann Strauss I"): "Q2",
    ("es", "Cited Twice"): "Q40",
    ("en", "La Scala"): "Q5471",
    ("en", "Welsh National Opera"): "Q577000",
    ("en", "Vienna Philharmonic"): "Q154685",
}

ITEMS = {
    "Q7349": WikidataItem(
        "Q7349", "Joseph Haydn (Wikidata)", frozenset({"Q5"}), frozenset({1732}), frozenset({1809})
    ),
    "Q1": WikidataItem("Q1", "Fidelio", frozenset({"Q1344"}), frozenset(), frozenset()),
    "Q2": WikidataItem("Q2", "Johann Strauss I", frozenset({"Q5"}), frozenset({1804}), frozenset({1849})),
    "Q10": WikidataItem(
        "Q10", "Josef Suk (Wikidata)", frozenset({"Q5"}), frozenset({1874}), frozenset({1935})
    ),
    "Q11": WikidataItem("Q11", "Josef Suk", frozenset({"Q5"}), frozenset({1929}), frozenset({2014})),
    "Q20": WikidataItem("Q20", "G. Rossi", frozenset({"Q5"}), frozenset({1700}), frozenset({1760})),
    "Q21": WikidataItem("Q21", "Giovanni Rossi", frozenset({"Q5"}), frozenset({1701}), frozenset({1759})),
    "Q22": WikidataItem("Q22", "A painting", frozenset({"Q3305213"}), frozenset({1800}), frozenset({1860})),
    "Q30": WikidataItem("Q30", "Twin", frozenset({"Q5"}), frozenset({1600}), frozenset({1660})),
    "Q40": WikidataItem("Q40", "Cited", frozenset({"Q5"}), frozenset({1500}), frozenset({1560})),
    "Q5471": WikidataItem("Q5471", "La Scala", frozenset({"Q153562"}), frozenset(), frozenset()),
    "Q577000": WikidataItem("Q577000", "WNO", frozenset({"Q20819922"}), frozenset(), frozenset()),
    "Q154685": WikidataItem("Q154685", "Wiener", frozenset({"Q42998"}), frozenset(), frozenset()),
}

SEARCHES = {
    "Josef Suk": ["Q10", "Q11"],
    "Giovanni Rossi": ["Q20", "Q21", "Q22"],
    "Twin Page": ["Q30"],
    "Cited Twice": ["Q40"],
}


@pytest.fixture
def documents(monkeypatch: pytest.MonkeyPatch) -> list[Doc]:
    def iter_pages(
        session: object, refs: Iterable[PageRef], max_pages: int | None = None
    ) -> Iterator[tuple[PageRef, str]]:
        for ref in refs:
            yield ref, PAGES[(ref.section, ref.slug)]

    refs = [PageRef(section, slug, f"https://x/{section}/{slug}") for section, slug in PAGES]
    module = "composer_scrapers.classicapedia"
    monkeypatch.setattr(f"{module}.fetch_sitemap", lambda session: "")
    monkeypatch.setattr(f"{module}.page_refs", lambda xml: refs)
    monkeypatch.setattr(f"{module}.fetch_index", lambda session: "")
    monkeypatch.setattr(f"{module}.parse_index", lambda script: ENTRIES)
    monkeypatch.setattr(f"{module}.iter_pages", iter_pages)
    monkeypatch.setattr(
        f"{module}.wikipedia_qids", lambda session, articles: {a: QIDS[a] for a in articles if a in QIDS}
    )
    monkeypatch.setattr(f"{module}.search_people", lambda session, name: SEARCHES.get(name, []))
    monkeypatch.setattr(f"{module}.wikidata_items", lambda session, qids: {q: ITEMS[q] for q in qids})
    return list(ClassicapediaAdapter().fetch())


def _entity(docs: list[Doc], doc_id: str) -> EntityDocument:
    found = [doc for doc in docs if isinstance(doc, EntityDocument) and doc.id == doc_id]
    assert len(found) == 1
    return found[0]


def _ids(docs: list[Doc]) -> set[str]:
    return {doc.id for doc in docs if isinstance(doc, EntityDocument)}


def _objects(entity: EntityDocument, predicate: str) -> list[tuple[str | None, str | None]]:
    return [(c.object_label, c.object_wikidata_id) for c in entity.claims if c.predicate == predicate]


def _values(entity: EntityDocument, predicate: str) -> list[str | None]:
    return [c.value for c in entity.claims if c.predicate == predicate]


def test_the_adapter_is_registered() -> None:
    assert isinstance(REGISTRY["classicapedia"], ClassicapediaAdapter)


def test_a_confirmed_person_is_pinned_by_url_and_takes_wikidatas_name(documents: list[Doc]) -> None:
    haydn = _entity(documents, "b/haydn")
    assert haydn.url == "https://www.wikidata.org/wiki/Q7349"
    assert haydn.name == "Joseph Haydn (Wikidata)"
    assert haydn.raw["page_url"] == "https://classicapedia.com/en/b/haydn.html"
    assert haydn.raw["site_name"] == "Joseph Haydn"


def test_a_cited_article_that_is_not_the_person_does_not_pin(documents: list[Doc]) -> None:
    beethoven = _entity(documents, "b/beethoven")
    assert beethoven.url == "https://classicapedia.com/en/b/beethoven.html"
    assert beethoven.name == "Ludwig van Beethoven"


def test_a_person_claims_the_facts_the_page_states(documents: list[Doc]) -> None:
    haydn = _entity(documents, "b/haydn")
    assert _values(haydn, "born_on") == ["1732"]
    assert _values(haydn, "died_on") == ["1809"]
    assert _objects(haydn, "born_in") == [("Rohrau", None)]
    assert _objects(haydn, "has_profession") == [("composer", None), ("teacher", None)]


def test_no_prose_reaches_raw(documents: list[Doc]) -> None:
    assert {"page_url", "site_name", "dates", "occupation", "teachers", "pupils"} <= set(
        _entity(documents, "b/haydn").raw
    )
    assert not {"bio", "text", "biography"} & set(_entity(documents, "b/haydn").raw)


def test_a_linked_pupil_is_named_as_their_own_record_is(documents: list[Doc]) -> None:
    """The index's name, not the link text, so both land on one entity."""
    assert ("Ludwig van Beethoven", None) in _objects(_entity(documents, "b/haydn"), "teacher_of")


def test_a_pupil_listed_twice_is_one_claim_and_nobody_teaches_themselves(documents: list[Doc]) -> None:
    pupils = _objects(_entity(documents, "b/haydn"), "teacher_of")
    assert pupils.count(("Ludwig van Beethoven", None)) == 1
    assert ("Joseph Haydn (Wikidata)", "Q7349") not in pupils


def test_a_shared_name_without_a_qid_is_dropped_everywhere(documents: list[Doc]) -> None:
    """Two people on one label would merge: a missing link is the lesser harm."""
    assert "b/strauss-ii" not in _ids(documents)
    assert ("Johann Strauss", None) not in _objects(_entity(documents, "b/haydn"), "teacher_of")


def test_a_shared_name_with_a_confirmed_qid_is_kept(documents: list[Doc]) -> None:
    pinned = _entity(documents, "b/strauss-pinned")
    assert pinned.url == "https://www.wikidata.org/wiki/Q2"
    assert ("Johann Strauss I", "Q2") in _objects(_entity(documents, "b/haydn"), "teacher_of")


def test_an_unlinked_name_stays_in_raw_even_if_the_site_has_a_page_for_it(documents: list[Doc]) -> None:
    """A name match gives namesakes and the page's own subject: 16% impossible in the full crawl."""
    haydn = _entity(documents, "b/haydn")
    pupils = _objects(haydn, "teacher_of")
    assert ("Czerny Unlinked", None) not in pupils
    assert ("Held", None) not in pupils
    assert "b/czerny" in _ids(documents)  # the person still has their own record
    named = [relation["name"] for relation in haydn.raw["pupils"]]
    assert {"Czerny Unlinked", "Held", "Joseph Haydn"} <= set(named)


def test_a_shared_name_is_recovered_when_one_item_fits_both_years(documents: list[Doc]) -> None:
    suk = _entity(documents, "b/suk")
    assert suk.url == "https://www.wikidata.org/wiki/Q10"
    assert suk.name == "Josef Suk (Wikidata)"
    assert ("Josef Suk (Wikidata)", "Q10") in _objects(_entity(documents, "b/haydn"), "teacher_of")


def test_a_page_without_both_years_is_not_searched_for(documents: list[Doc]) -> None:
    """Suk's namesake states a birth year only, which Q11 fits, but that is too little."""
    assert "b/suk-son" not in _ids(documents)


def test_a_recovered_person_on_a_shared_name_still_has_no_works(documents: list[Doc]) -> None:
    assert "Asrael" not in {doc.title for doc in documents if isinstance(doc, WorkMentionDocument)}


def test_several_fitting_items_pin_nobody(documents: list[Doc]) -> None:
    assert "b/many-a" not in _ids(documents)


def test_a_search_result_must_be_a_human(documents: list[Doc]) -> None:
    assert "b/many-b" not in _ids(documents)


def test_two_pages_reaching_one_item_pin_neither(documents: list[Doc]) -> None:
    assert not {"b/twin-a", "b/twin-b"} & _ids(documents)


def test_an_item_a_page_already_cites_cannot_be_found_again(documents: list[Doc]) -> None:
    assert _entity(documents, "b/pinned-a").url == "https://www.wikidata.org/wiki/Q40"
    assert "b/pinned-b" not in _ids(documents)


def test_an_unlinked_shared_name_is_dropped(documents: list[Doc]) -> None:
    pupils = _objects(_entity(documents, "b/haydn"), "teacher_of")
    assert pupils.count(("Johann Strauss", None)) == 0


def test_a_family_is_not_a_person(documents: list[Doc]) -> None:
    assert "b/haas" not in _ids(documents)


def test_a_composers_works_become_mentions_without_the_key(documents: list[Doc]) -> None:
    mentions = [doc for doc in documents if isinstance(doc, WorkMentionDocument)]
    assert [(m.composer, m.title) for m in mentions] == [("Joseph Haydn", "Symphony no.94")]
    assert mentions[0].raw["listed_as"] == "Symphony no.94, G"
    assert mentions[0].raw["years"] == "1791"
    assert mentions[0].url == "https://classicapedia.com/en/b/haydn.html"


def test_a_pinned_person_on_a_shared_name_has_no_works(documents: list[Doc]) -> None:
    """A mention finds its composer by label alone, so its waltzes could be anyone's."""
    titles = {doc.title for doc in documents if isinstance(doc, WorkMentionDocument)}
    assert "A march" not in titles


def test_a_venue_is_pinned_and_claims_its_place_and_article(documents: list[Doc]) -> None:
    scala = _entity(documents, "t/scala")
    assert scala.kind == "venue"
    assert scala.url == "https://www.wikidata.org/wiki/Q5471"
    assert _objects(scala, "based_in") == [("Milan", None)]
    assert _values(scala, "opened_on") == ["1778"]
    assert _values(scala, "wikipedia_article") == ["https://en.wikipedia.org/wiki/La_Scala"]
    assert scala.raw["country"] == "Italy"
    assert scala.raw["facts"]["Opened"] == "1778"


def test_a_company_filed_under_opera_houses_is_an_ensemble(documents: list[Doc]) -> None:
    assert _entity(documents, "t/wno").kind == "ensemble"


def test_a_venue_without_an_article_keeps_the_site_url(documents: list[Doc]) -> None:
    hall = _entity(documents, "s/hall")
    assert hall.kind == "venue"
    assert hall.url == "https://classicapedia.com/en/s/hall.html"


def test_an_orchestra_is_an_ensemble_on_its_item(documents: list[Doc]) -> None:
    wien = _entity(documents, "o/wien")
    assert wien.kind == "ensemble"
    assert wien.url == "https://www.wikidata.org/wiki/Q154685"
    assert _values(wien, "founded_on") == ["1842"]


def test_documents_survive_serialisation(documents: list[Doc]) -> None:
    for doc in documents:
        json.dumps(doc.raw)


@pytest.mark.parametrize(
    ("section", "classes", "kind"),
    [
        ("t", {"Q153562"}, "venue"),
        ("t", {"Q153562", "Q20819922"}, "venue"),  # a house that is also its company
        ("t", {"Q20819922"}, "ensemble"),
        ("s", {"Q3918"}, "venue"),  # no better idea: counted in the run's log
        ("o", {"Q163740"}, "ensemble"),
    ],
)
def test_classification(section: str, classes: set[str], kind: str) -> None:
    item = WikidataItem("Q1", None, frozenset(classes), frozenset(), frozenset())
    assert classify_kind(section, item) == kind


def test_no_item_means_the_section_decides() -> None:
    assert classify_kind("t", None) == "venue"
    assert classify_kind("o", None) == "ensemble"


def test_shared_names_ignore_case_and_accents() -> None:
    entries = [
        IndexEntry("a", "Antonín Dvořák", (), ""),
        IndexEntry("b", "antonin dvorak", (), ""),
        IndexEntry("c", "Someone Else", (), ""),
    ]
    assert shared_names(entries) == {name_key("Antonín Dvořák")}


def test_an_empty_site_fails_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    module = "composer_scrapers.classicapedia"
    monkeypatch.setattr(f"{module}.fetch_sitemap", lambda session: "")
    monkeypatch.setattr(f"{module}.fetch_index", lambda session: "window.INDICE=[]")
    with pytest.raises(RuntimeError, match="no biographies"):
        list(ClassicapediaAdapter().fetch())
