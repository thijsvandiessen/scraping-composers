"""Tests for the roh adapter — the four-tier sweep and the documents it emits.

The HTTP layer is stubbed at the names ``composer_scrapers.roh`` imported, so
these exercise the walk and the document shapes without a network.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from composer_scrapers import REGISTRY, EntityDocument, WorkMentionDocument
from composer_scrapers.roh import RohAdapter
from composer_scrapers.roh.urls import page_ref
from test_roh import panel  # the shared page skeleton

# ---- a miniature database: two works, one staged, one with a loose night ---- #

INDEX_A = """<table class="results">
<tr class="odd"><td class="genre">Ballet</td><td class="title">
<a href='work.aspx?work=301&amp;row=0&amp;letter=A&amp;'>Adagio Hammerklavier</a>
(Hans van Manen)</td></tr>
<tr class="even"><td class="genre">Opera</td><td class="title">
<a href='work.aspx?work=551&amp;row=1&amp;letter=A&amp;'>Aida</a> (Giuseppe Verdi)</td></tr>
<tr class="odd"><td class="genre">Opera</td><td class="title">
<a href='work.aspx?work=12194&amp;row=2&amp;letter=A&amp;'>The Barber of Seville: see
'Il barbiere di Siviglia'</a></td></tr>
</table>"""

BALLET_WORK = panel("""<H1 class=large>Adagio Hammerklavier</H1>
<H2>Ballet: Work details</H2>
<TABLE class="result work">
<tr><th>Choreographer:</th><td>Hans van Manen </td></tr>
<tr><th>Composer:</th><td>Ludwig van Beethoven </td></tr>
<tr><th>Music title:</th><td>Piano Sonata No. 29 in B-flat, Op. 106 - Slow movement</td></tr>
<TR><TH>World premiere:</TH><TD>4 October 1973, Dutch National Ballet, Amsterdam</TD></tr>
</TABLE>
<table class="results production">
<tr><th colspan="2">Productions</th></tr>
<tr class="odd"><td><a href='production.aspx?production=4310&amp;row=0'>Adagio Hammerklavier (1976)</a></td>
<td>The Royal Ballet (1 performance online)</td></tr>
</table>""")

OPERA_WORK = panel("""<H1 class=large>Aida</H1>
<H2>Opera: Work details</H2>
<TABLE class="result work">
<tr><th>Composer:</th><td>Giuseppe Verdi </td></tr>
<tr><th>Music title:</th><td>Aida (1871)</td></tr>
</TABLE>
<table class="results performance">
<tr><th colspan="3">Performances not linked to a production</th></tr>
<tr class="odd"><td><a href='performance.aspx?performance=18316'>28 June 1988</a></td>
<td>Evening </td><td>Royal Opera House, Covent Garden, London</td></tr>
</table>""")

STUB_WORK = panel(
    "<H1 class=large>The Barber of Seville: see 'Il barbiere di Siviglia'</H1>"
    '<H2>Opera: Work details</H2><TABLE class="result work"></TABLE>'
)

PRODUCTION = panel("""<H1 class=large>Adagio Hammerklavier (1976)</H1>
<H2>Ballet: Production details</H2>
<TABLE class="result work">
<TR><TH>Company:</TH><TD>The Royal Ballet</TD></tr>
<tr><th>Set designer:</th><td>Jean-Paul Vroom </td></tr>
</TABLE>
<table class="results performance">
<tr><th colspan="3">Performances</th></tr>
<tr class="odd"><td><a href='performance.aspx?performance=4311&amp;row=0'>23 November 1976</a></td>
<td>Evening 7.30pm</td><td>Royal Opera House, Covent Garden, London</td></tr>
</table>""")

BALLET_NIGHT = panel("""<H1 class="large">Adagio Hammerklavier-23 November 1976 Evening 7.30pm</H1>
<H2>Ballet: Performance details</H2>
<TABLE class="result">
<TR><TH>Company:</TH><TD>The Royal Ballet</TD></tr>
<tr><th>Conductor</th><td>Ashley Lawrence</td></tr>
</TABLE>
<div class="personresults"><table class="results performance">
<tr><th colspan="3">Cast</th></tr>
<tr class="odd"><td>Soloist<i></i></td><td></td><td>Vergie Derman</td></tr>
<tr class="even"><td><i></i></td><td></td><td>The Orchestra of the Royal Opera House</td></tr>
</table></div>""")

OPERA_NIGHT = panel("""<H1 class="large">Aida - Act IV scene 1-28 June 1988 Evening </H1>
<P><a id="ContentPlaceHolderBody_uiPerfLinkAll" href="Work.aspx?work=551">List All</a></P>
<H2>Opera: Performance details</H2>
<TABLE class="result">
<TR><TH>Venue:</TH><TD>Royal Opera House, Covent Garden, London</TD></tr>
<tr><th>Conductor</th><td>John Barker</td></tr>
</TABLE>
<div class="personresults"><table class="results performance">
<tr><th colspan="3">Cast</th></tr>
<tr class="odd"><td>Radam&#232;s<i></i></td><td></td><td>Pl&#225;cido Domingo</td></tr>
</table></div>""")

PAGES: dict[tuple[str, int], str] = {
    ("work", 301): BALLET_WORK,
    ("work", 551): OPERA_WORK,
    ("work", 12194): STUB_WORK,
    ("production", 4310): PRODUCTION,
    ("performance", 4311): BALLET_NIGHT,
    ("performance", 18316): OPERA_NIGHT,
}


@pytest.fixture(autouse=True)
def stub_http(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Serve the miniature database, recording the order pages were asked for."""
    asked: list[str] = []

    def index(_client: object, letter: str) -> str:
        return INDEX_A if letter == "A" else "<table class='results'></table>"

    def page(_client: object, url: str, _cache: object = None) -> str | None:
        asked.append(url)
        ref = page_ref(url)
        return PAGES.get(ref) if ref else None

    monkeypatch.setattr("composer_scrapers.roh.fetch_index", index)
    monkeypatch.setattr("composer_scrapers.roh.fetch_page", page)
    monkeypatch.setattr("composer_scrapers.roh.make_client", lambda: _NullClient())
    monkeypatch.setattr("composer_scrapers.roh.open_page_cache", lambda: None)
    return asked


class _NullClient:
    def __enter__(self) -> _NullClient:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


def _run(max_pages: int | None = None) -> list[Any]:
    return list(RohAdapter().fetch(max_pages=max_pages))


def _mentions(docs: list[Any]) -> list[WorkMentionDocument]:
    return [d for d in docs if isinstance(d, WorkMentionDocument)]


def _people(docs: list[Any]) -> list[EntityDocument]:
    return [d for d in docs if isinstance(d, EntityDocument)]


# ---- registration ---- #


def test_the_source_is_registered_under_its_own_name() -> None:
    assert isinstance(REGISTRY["roh"], RohAdapter)
    assert REGISTRY["roh"].base_url == "https://www.rohcollections.org.uk"


# ---- what a sweep emits ---- #


def test_a_sweep_emits_a_mention_per_work_and_one_per_night() -> None:
    """Nights hanging off a work are queued in the work tier, staged ones in the next."""
    ids = [m.id for m in _mentions(_run())]
    assert ids == ["work/301", "work/551", "performance/18316", "performance/4311"]


def test_a_cross_reference_stub_emits_nothing() -> None:
    assert "work/12194" not in [m.id for m in _mentions(_run())]


def test_a_ballet_night_carries_the_composer_from_the_work_above_it() -> None:
    """Nothing on a performance page names one — it can come from nowhere else."""
    night = next(m for m in _mentions(_run()) if m.id == "performance/4311")
    assert night.composer == "Ludwig van Beethoven"
    assert night.title == "Piano Sonata No. 29 in B-flat, Op. 106 - Slow movement"
    assert night.raw["staged_title"] == "Adagio Hammerklavier"


def test_a_night_carries_the_date_venue_and_cast_of_the_evening() -> None:
    night = next(m for m in _mentions(_run()) if m.id == "performance/18316")
    assert night.raw["date"] == "1988-06-28"
    assert night.raw["date_stated"] == "28 June 1988"
    assert night.raw["venue"] == "Royal Opera House, Covent Garden, London"
    assert night.raw["conductors"] == ["John Barker"]
    assert night.raw["cast"][0]["name"] == "Plácido Domingo"
    assert night.raw["performed_title"] == "Aida - Act IV scene 1"


def test_a_works_premieres_are_split_into_a_date_and_a_place() -> None:
    work = next(m for m in _mentions(_run()) if m.id == "work/301")
    assert work.raw["premieres"]["World premiere"] == {
        "stated": "4 October 1973, Dutch National Ballet, Amsterdam",
        "date": "1973-10-04",
        "place": "Dutch National Ballet, Amsterdam",
    }


def test_a_work_records_the_productions_it_was_staged_in() -> None:
    work = next(m for m in _mentions(_run()) if m.id == "work/301")
    assert work.raw["productions"] == [
        {
            "production_id": 4310,
            "title": "Adagio Hammerklavier (1976)",
            "companies": "The Royal Ballet",
            "performances": 1,
        }
    ]


def test_every_document_survives_the_round_trip_to_disk() -> None:
    """The raw payloads are what the bronze bucket writes as NDJSON."""
    from composer_schema import deserialize_document, serialize_document

    for doc in _run():
        assert deserialize_document(json.loads(json.dumps(serialize_document(doc)))) == doc


def test_every_document_names_the_source_and_a_reachable_url() -> None:
    for doc in _mentions(_run()):
        assert doc.source_name == "roh"
        assert doc.url is not None and doc.url.startswith("https://www.rohcollections.org.uk/")


# ---- the tiers, and a run cut short ---- #


def test_the_work_tier_is_drained_before_anything_descends(stub_http: list[str]) -> None:
    """Every composer is known at ~1,000 pages of ~18,000; the rest is detail."""
    _run()
    kinds = [page_ref(url)[0] for url in stub_http if page_ref(url)]  # type: ignore[index]
    assert kinds == ["work", "work", "work", "production", "performance", "performance"]


def test_a_run_capped_at_the_work_tier_still_returns_every_composer() -> None:
    """The budget caps record pages, so a short run loses nights, never composers."""
    docs = _run(max_pages=3)
    assert [m.id for m in _mentions(docs)] == ["work/301", "work/551"]
    composers = {p.name for p in _people(docs) if any(c.object_label == "composer" for c in p.claims)}
    assert composers == {"Ludwig van Beethoven", "Giuseppe Verdi"}


def test_the_index_is_never_capped(stub_http: list[str]) -> None:
    """It is the only enumerator; a short run should still know what it skipped."""
    assert [m.id for m in _mentions(_run(max_pages=1))] == ["work/301"]


# ---- the people ---- #


def test_a_night_whose_back_link_disagrees_with_the_walk_is_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The only check on the one part of this source that is inferred, not read."""
    stray = OPERA_NIGHT.replace('href="Work.aspx?work=551"', 'href="Work.aspx?work=999"')
    with caplog.at_level("WARNING"), patch.dict(PAGES, {("performance", 18316): stray}):
        _run()
    assert "walked from ('work', 551)" in caplog.text


def test_a_night_reached_through_a_production_agrees_with_its_back_link(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.clear()
    with caplog.at_level("WARNING"):
        _run()
    assert "links back to" not in caplog.text


def test_a_person_is_a_composer_only_because_a_work_page_said_so() -> None:
    people = {p.name: p for p in _people(_run())}
    assert [(c.predicate, c.object_label) for c in people["Ludwig van Beethoven"].claims] == [
        ("has_profession", "composer")
    ]
    assert [(c.predicate, c.object_label) for c in people["Hans van Manen"].claims] == [
        ("has_profession", "choreographer")
    ]


def test_a_cast_member_is_credited_without_being_given_a_profession() -> None:
    """The cast column holds the part, not the discipline — "Radamès" is not a job."""
    domingo = next(p for p in _people(_run()) if p.name == "Plácido Domingo")
    assert domingo.claims == ()
    assert domingo.raw["credited_as"] == {"Cast": 1}
    assert domingo.raw["parts"] == ["Radamès"]


def test_a_designer_is_recorded_without_being_given_a_profession() -> None:
    vroom = next(p for p in _people(_run()) if p.name == "Jean-Paul Vroom")
    assert vroom.claims == ()
    assert vroom.raw["credited_as"] == {"Set designer": 1}


def test_an_orchestra_in_the_cast_list_is_typed_as_an_ensemble() -> None:
    """Letting one through as a person puts a chorus into the person dedupe pass."""
    orchestra = next(p for p in _people(_run()) if p.name.startswith("The Orchestra"))
    assert orchestra.kind == "ensemble"
    assert orchestra.claims == ()


def test_a_persons_credits_are_counted_across_the_whole_sweep() -> None:
    conductor = next(p for p in _people(_run()) if p.name == "John Barker")
    assert conductor.raw["performances"] == 1
    assert conductor.raw["first_performance"] == "1988-06-28"
    assert conductor.raw["genres"] == ["Opera"]


def test_people_are_emitted_once_and_last() -> None:
    docs = _run()
    names = [p.name for p in _people(docs)]
    assert len(names) == len(set(names))
    assert all(isinstance(d, EntityDocument) for d in docs[len(_mentions(docs)) :])
