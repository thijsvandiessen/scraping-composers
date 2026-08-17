"""Tests for the imslp_works HTTP fetch layer: category resolution, section
splitting, per-section pagination, and the gold-driven work-page walk.

The page shapes below mirror what was confirmed live against a real
multi-page composer (Bach) while building this source: a composer's category
page holds several ``<h3 class='nojs'>Section (count)</h3>`` sections, and a
followed "next 200" pagination link can land back on a full composer page
(same title, re-split by section) or on a bare single-category listing (no
section markers at all, handled as a fallback).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from composer_scrapers.imslp_works.fetch import (
    BASE_URL,
    category_url,
    iter_section_work_paths,
    iter_work_pages,
    resolve_category_url,
    work_paths,
)
from composer_scrapers.imslp_works.gold import GoldComposer


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Politeness delays are real seconds; drop them for the suite."""
    monkeypatch.setattr("composer_scrapers.imslp_works.fetch.time.sleep", lambda _: None)
    monkeypatch.setattr("composer_http.time.sleep", lambda _: None)


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# A composer whose Compositions section spans two "next 200" pages, plus a
# Collaborations section (excluded) and a one-page Collected Works section.
CATEGORY_PAGE = """
<h3 class='nojs'>Compositions (2)</h3>
<h2>Compositions by: Test, Composer</h2>
<a href="/wiki/Work_A_(Test,_Composer)">Work A</a>
<a href="/index.php?title=Category:Test,_Composer&amp;pagefrom=X" class="categorypaginglink">next 200</a>
<h3 class='nojs'>Collaborations (1)</h3>
<h2>Collaborations with: Test, Composer</h2>
<a href="/wiki/Other_Work_(Someone_Else)">Other Work</a>
<h3 class='nojs'>Collected Works (1)</h3>
<h2>Collected works: Test, Composer</h2>
<a href="/wiki/Complete_Works_(Test,_Composer)">Complete Works</a>
<a href="/wiki/Category:Somewhere">not a work</a>
"""

# The main-title continuation: MediaWiki re-renders the *whole* composer page
# again, with the Compositions section advanced and no further "next 200".
CONTINUATION_SAME_TITLE = """
<h3 class='nojs'>Compositions (2)</h3>
<h2>Compositions by: Test, Composer</h2>
<a href="/wiki/Work_B_(Test,_Composer)">Work B</a>
<h3 class='nojs'>Collaborations (1)</h3>
<h2>Collaborations with: Test, Composer</h2>
<a href="/wiki/Other_Work_(Someone_Else)">Other Work</a>
<h3 class='nojs'>Collected Works (1)</h3>
<h2>Collected works: Test, Composer</h2>
<a href="/wiki/Complete_Works_(Test,_Composer)">Complete Works</a>
"""

CATEGORY_KEY = "/wiki/Category:Test,_Composer"
CONTINUATION_KEY = "/index.php?title=Category:Test,_Composer&pagefrom=X"


def _category_handler(request: httpx.Request) -> httpx.Response:
    key = request.url.path + (f"?{request.url.query.decode()}" if request.url.query else "")
    if key == CATEGORY_KEY:
        return httpx.Response(200, text=CATEGORY_PAGE)
    if key == CONTINUATION_KEY:
        return httpx.Response(200, text=CONTINUATION_SAME_TITLE)
    return httpx.Response(404, text="not found")


# ---------------------------------------------------------------------------
# category_url / resolve_category_url
# ---------------------------------------------------------------------------


def test_category_url_matches_imslps_surname_given_convention() -> None:
    assert category_url("Beethoven, Ludwig van") == BASE_URL + "/wiki/Category:Beethoven,_Ludwig_van"


def test_resolve_category_url_prefers_the_known_url() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="ok")

    client = _client(handler)
    known = BASE_URL + "/wiki/Category:Known,_Composer"
    url = resolve_category_url(client, "Unused, Label", known)

    assert url == known
    assert seen == [known]


def test_resolve_category_url_constructs_and_verifies_when_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    client = _client(handler)
    url = resolve_category_url(client, "New, Composer", None)

    assert url == BASE_URL + "/wiki/Category:New,_Composer"


def test_resolve_category_url_is_none_when_the_guess_does_not_resolve() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    client = _client(handler)
    assert resolve_category_url(client, "Nobody, Really", None) is None


# ---------------------------------------------------------------------------
# work_paths: link discovery within a section
# ---------------------------------------------------------------------------


def test_work_paths_extracts_wiki_links() -> None:
    html = '<a href="/wiki/Work_A_(X)">Work A</a><a href="/wiki/Work_B_(X)">Work B</a>'
    assert work_paths(html) == ["Work_A_(X)", "Work_B_(X)"]


def test_work_paths_excludes_non_work_namespaces() -> None:
    html = (
        '<a href="/wiki/Category:Sonatas">cat</a>'
        '<a href="/wiki/Special:Search">search</a>'
        '<a href="/wiki/Work_A_(X)">Work A</a>'
    )
    assert work_paths(html) == ["Work_A_(X)"]


def test_work_paths_deduplicates() -> None:
    html = '<a href="/wiki/Work_A_(X)">Work A</a><a href="/wiki/Work_A_(X)">again</a>'
    assert work_paths(html) == ["Work_A_(X)"]


# ---------------------------------------------------------------------------
# iter_section_work_paths: section scoping + pagination
# ---------------------------------------------------------------------------


def test_iter_section_work_paths_only_walks_compositions_and_collected_works() -> None:
    client = _client(_category_handler)
    paths = list(iter_section_work_paths(client, BASE_URL + CATEGORY_KEY))

    assert "Other_Work_(Someone_Else)" not in paths


def test_iter_section_work_paths_follows_next_200_within_a_section() -> None:
    client = _client(_category_handler)
    paths = list(iter_section_work_paths(client, BASE_URL + CATEGORY_KEY))

    assert paths == ["Work_A_(Test,_Composer)", "Work_B_(Test,_Composer)", "Complete_Works_(Test,_Composer)"]


def test_iter_section_work_paths_stops_when_next_link_repeats(monkeypatch: pytest.MonkeyPatch) -> None:
    """A self-referential "next 200" link would otherwise loop until MAX_SECTION_PAGES."""
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            200,
            text=(
                "<h3 class='nojs'>Compositions (1)</h3><h2>Compositions by: X</h2>"
                '<a href="/wiki/Work_(X)">Work</a>'
                '<a href="/wiki/Category:X" class="categorypaginglink">next 200</a>'
            ),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    paths = list(iter_section_work_paths(client, BASE_URL + "/wiki/Category:X"))

    assert paths == ["Work_(X)"]
    assert len(requests) == 1


# ---------------------------------------------------------------------------
# iter_work_pages: gold-driven walk
# ---------------------------------------------------------------------------


def _fake_gold_composers(monkeypatch: pytest.MonkeyPatch, people: list[GoldComposer]) -> None:
    monkeypatch.setattr("composer_scrapers.imslp_works.fetch.gold_composers", lambda _path: people)


def _fake_new_client(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    monkeypatch.setattr(
        "composer_scrapers.imslp_works.fetch.new_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _full_handler(request: httpx.Request) -> httpx.Response:
    key = request.url.path + (f"?{request.url.query.decode()}" if request.url.query else "")
    if key == CATEGORY_KEY:
        return httpx.Response(200, text=CATEGORY_PAGE)
    if key == CONTINUATION_KEY:
        return httpx.Response(200, text=CONTINUATION_SAME_TITLE)
    if key.startswith("/wiki/Work_") or key.startswith("/wiki/Complete_Works"):
        return httpx.Response(200, text=f"<title>{key.rsplit('/', 1)[1]} - IMSLP</title>")
    return httpx.Response(404, text="not found")


COMPOSER = GoldComposer(entity_id="c1", label="Test, Composer", known_imslp_url=BASE_URL + CATEGORY_KEY)


def test_iter_work_pages_yields_composer_path_url_html_per_work(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_gold_composers(monkeypatch, [COMPOSER])
    _fake_new_client(monkeypatch, _full_handler)

    pages = list(iter_work_pages("unused-gold.db"))

    assert [p for _, p, _, _ in pages] == [
        "Work_A_(Test,_Composer)",
        "Work_B_(Test,_Composer)",
        "Complete_Works_(Test,_Composer)",
    ]
    assert all(composer == COMPOSER for composer, _, _, _ in pages)
    assert pages[0][2] == BASE_URL + "/wiki/Work_A_(Test,_Composer)"
    assert "Work_A_(Test,_Composer)" in pages[0][3]


def test_iter_work_pages_skips_composers_whose_category_does_not_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unresolved = GoldComposer(entity_id="c2", label="Nobody, Really", known_imslp_url=None)
    _fake_gold_composers(monkeypatch, [unresolved])
    _fake_new_client(monkeypatch, lambda request: httpx.Response(404, text="not found"))

    assert list(iter_work_pages("unused-gold.db")) == []


def test_iter_work_pages_honours_max_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_gold_composers(monkeypatch, [COMPOSER])
    _fake_new_client(monkeypatch, _full_handler)

    pages = list(iter_work_pages("unused-gold.db", max_pages=2))

    assert [p for _, p, _, _ in pages] == ["Work_A_(Test,_Composer)", "Work_B_(Test,_Composer)"]


def test_iter_work_pages_caps_across_composers_combined(monkeypatch: pytest.MonkeyPatch) -> None:
    other = GoldComposer(entity_id="c2", label="Other, Composer", known_imslp_url=BASE_URL + CATEGORY_KEY)
    _fake_gold_composers(monkeypatch, [COMPOSER, other])
    _fake_new_client(monkeypatch, _full_handler)

    pages = list(iter_work_pages("unused-gold.db", max_pages=1))

    assert len(pages) == 1


def test_iter_work_pages_skips_to_next_composer_after_a_bot_check_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors the real IMSLP failure: a "next 200" continuation redirects to
    the site's own bot-check interstitial (/friendlytest.html) instead of
    serving the page. That must not abort composers after the broken one."""
    blocked = GoldComposer(entity_id="c1", label="Blocked, Composer", known_imslp_url=BASE_URL + CATEGORY_KEY)
    other = GoldComposer(
        entity_id="c2", label="Other, Composer", known_imslp_url=BASE_URL + "/wiki/Category:Other,_Composer"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path + (f"?{request.url.query.decode()}" if request.url.query else "")
        if key == CATEGORY_KEY:
            return httpx.Response(200, text=CATEGORY_PAGE)
        if key == CONTINUATION_KEY:
            return httpx.Response(302, headers={"Location": "/friendlytest.html"})
        if key == "/wiki/Category:Other,_Composer":
            return httpx.Response(
                200,
                text=(
                    "<h3 class='nojs'>Compositions (1)</h3><h2>Compositions by: Other, Composer</h2>"
                    '<a href="/wiki/Work_C_(Other,_Composer)">Work C</a>'
                ),
            )
        if key.startswith("/wiki/Work_"):
            return httpx.Response(200, text=f"<title>{key.rsplit('/', 1)[1]} - IMSLP</title>")
        return httpx.Response(404, text="not found")

    _fake_gold_composers(monkeypatch, [blocked, other])
    _fake_new_client(monkeypatch, handler)

    pages = list(iter_work_pages("unused-gold.db"))

    assert [p for _, p, _, _ in pages] == ["Work_A_(Test,_Composer)", "Work_C_(Other,_Composer)"]
