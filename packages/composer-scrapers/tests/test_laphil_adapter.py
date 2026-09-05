"""Tests for the laphil adapter: which composers the walk finds, and what it says
about them."""

from __future__ import annotations

import httpx
import pytest
from composer_scrapers import REGISTRY
from composer_scrapers.laphil import LaPhilAdapter

BASE = "https://www.laphil.com"


def _event(*credits: tuple[str | None, str], also_links: str = "") -> str:
    """An event page crediting each (slug, display) pair; slug None for a bare
    <span> credit, the shape used for a name with no page behind it."""
    items = "".join(
        (
            f'<div class="program-item "><h4 class="program-item__header">'
            f'<a href="/people/{slug}" class="program-item__composer program-item__composer--link">'
            f"{display}</a></h4></div>"
            if slug is not None
            else f'<div class="program-item "><h4 class="program-item__header">'
            f'<span class="program-item__composer">{display}</span></h4></div>'
        )
        for slug, display in credits
    )
    return f'<section class="element program-block">{items}</section>{also_links}'


def _person(name: str, *, job: str = "", bio: str = "", events: str = "") -> str:
    job_field = f',"jobTitle":"{job}"' if job else ""
    bio_block = f'<div class="artist-bio">{bio}</div>' if bio else ""
    return (
        f'<script type="application/ld+json">'
        f'{{"@graph":[{{"@type":"Person","name":"{name}"{job_field}}}]}}</script>'
        f"{bio_block}{events}"
    )


#: An event in the sitemap credits Beethoven; his page links an event that is
#: *not* in the sitemap, and that one credits Brahms. Finding Brahms is the walk
#: doing its job — the sitemap is capped and never mentions him.
SITE = {
    f"{BASE}/events/beethoven-7": _event(
        ("ludwig-van-beethoven", "BEETHOVEN"),
        (None, "EWALD"),
        also_links='<a href="/people/jhoanna-sierralta">conductor</a>',
    ),
    f"{BASE}/people/ludwig-van-beethoven": _person(
        "Ludwig van Beethoven",
        job="composer",
        bio="Born: 1770, Bonn, Germany Died: 1827, Vienna, Austria &ldquo;Quote.&rdquo;",
        events='<a href="/events/a-german-requiem">Requiem</a>',
    ),
    f"{BASE}/events/a-german-requiem": _event(("johannes-brahms", "BRAHMS")),
    f"{BASE}/people/johannes-brahms": _person("Johannes Brahms"),
    f"{BASE}/people/jhoanna-sierralta": _person("Jhoanna Sierralta", job="assistant conductor"),
}

SITEMAP = f"<urlset><url><loc>{BASE}/events/beethoven-7</loc></url></urlset>"


def _stub_site(monkeypatch: pytest.MonkeyPatch, site: dict[str, str], sitemap: str = SITEMAP) -> list[str]:
    """Serve *site* to the adapter, returning the list it records fetches in."""
    fetched: list[str] = []

    def fetch_page(_client: object, url: str, _cache: object = None) -> str | None:
        fetched.append(url)
        return site.get(url)

    monkeypatch.setattr("composer_scrapers.laphil.fetch_sitemap", lambda _client: sitemap)
    monkeypatch.setattr("composer_scrapers.laphil.fetch_page", fetch_page)
    monkeypatch.setattr("composer_scrapers.laphil.make_client", lambda: httpx.Client())
    return fetched


def test_laphil_is_registered() -> None:
    assert isinstance(REGISTRY["laphil"], LaPhilAdapter)
    assert REGISTRY["laphil"].name == "laphil"
    assert REGISTRY["laphil"].base_url == BASE


def test_walk_reaches_composers_the_sitemap_never_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_site(monkeypatch, SITE)
    docs = list(LaPhilAdapter().fetch())
    assert [d.name for d in docs] == ["Ludwig van Beethoven", "Johannes Brahms"]


def test_only_programme_credits_make_a_composer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The conductor is linked from the same event page and is not emitted; the
    unlinked "EWALD" credit has no page to stand on and is not either."""
    _stub_site(monkeypatch, SITE)
    names = [d.name for d in LaPhilAdapter().fetch()]
    assert "Jhoanna Sierralta" not in names
    assert "EWALD" not in names


def test_document_fields_and_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_site(monkeypatch, SITE)
    (beethoven, _brahms) = list(LaPhilAdapter().fetch())
    assert beethoven.id == "/people/ludwig-van-beethoven"
    assert beethoven.url == f"{BASE}/people/ludwig-van-beethoven"
    assert beethoven.source_name == "laphil"
    assert beethoven.kind == "person"
    assert [(c.predicate, c.object_label or c.value) for c in beethoven.claims] == [
        ("has_profession", "composer"),
        ("born_on", "1770"),
        ("died_on", "1827"),
        ("also_known_as", "BEETHOVEN"),
    ]
    assert beethoven.raw["born_place"] == "Bonn, Germany"
    assert beethoven.raw["job_title"] == "composer"


def test_a_composer_with_no_dates_claims_only_the_profession(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_site(monkeypatch, SITE)
    (_beethoven, brahms) = list(LaPhilAdapter().fetch())
    assert [c.predicate for c in brahms.claims] == ["has_profession", "also_known_as"]


def test_a_programme_name_differing_only_in_case_is_not_an_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = {
        f"{BASE}/events/e": _event(("jessie-montgomery", "Jessie MONTGOMERY")),
        f"{BASE}/people/jessie-montgomery": _person("Jessie Montgomery"),
    }
    _stub_site(monkeypatch, site, f"<urlset><url><loc>{BASE}/events/e</loc></url></urlset>")
    (doc,) = list(LaPhilAdapter().fetch())
    assert [c.predicate for c in doc.claims] == ["has_profession"]


def test_a_composer_credited_twice_is_read_and_emitted_once(monkeypatch: pytest.MonkeyPatch) -> None:
    site = {
        f"{BASE}/events/one": _event(("ludwig-van-beethoven", "BEETHOVEN")),
        f"{BASE}/events/two": _event(("ludwig-van-beethoven", "BEETHOVEN")),
        f"{BASE}/people/ludwig-van-beethoven": _person("Ludwig van Beethoven", job="composer"),
    }
    sitemap = (
        f"<urlset><url><loc>{BASE}/events/one</loc></url><url><loc>{BASE}/events/two</loc></url></urlset>"
    )
    fetched = _stub_site(monkeypatch, site, sitemap)
    docs = list(LaPhilAdapter().fetch())
    assert len(docs) == 1
    assert fetched.count(f"{BASE}/people/ludwig-van-beethoven") == 1


def test_a_page_that_calls_itself_a_composer_is_taken_at_its_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No programme in reach credits them, but the page says composer."""
    site = {
        f"{BASE}/people/saad-haddad": _person("Saad Haddad", job="composer"),
        f"{BASE}/people/andrea-caputo": _person("Andrea Caputo", job="Clarinet"),
    }
    sitemap = (
        f"<urlset><url><loc>{BASE}/people/saad-haddad</loc></url>"
        f"<url><loc>{BASE}/people/andrea-caputo</loc></url></urlset>"
    )
    _stub_site(monkeypatch, site, sitemap)
    assert [d.name for d in LaPhilAdapter().fetch()] == ["Saad Haddad"]


def test_an_ensemble_credited_as_composer_is_not_stored_as_a_person(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = {
        f"{BASE}/events/e": _event(("escher-string-quartet", "ESCHER")),
        f"{BASE}/people/escher-string-quartet": _person("Escher String Quartet"),
    }
    _stub_site(monkeypatch, site, f"<urlset><url><loc>{BASE}/events/e</loc></url></urlset>")
    (doc,) = list(LaPhilAdapter().fetch())
    assert doc.kind == "ensemble"


def test_an_unfetchable_page_does_not_end_the_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    """The previous LLM crawl of this site died mid-run on a dropped connection;
    a page that cannot be read costs that page and nothing else."""
    site = {
        f"{BASE}/events/one": _event(("gone", "GONE")),
        f"{BASE}/events/two": _event(("johannes-brahms", "BRAHMS")),
        f"{BASE}/people/johannes-brahms": _person("Johannes Brahms"),
    }
    sitemap = (
        f"<urlset><url><loc>{BASE}/events/one</loc></url><url><loc>{BASE}/events/two</loc></url></urlset>"
    )
    _stub_site(monkeypatch, site, sitemap)
    assert [d.name for d in LaPhilAdapter().fetch()] == ["Johannes Brahms"]


def test_max_pages_caps_the_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched = _stub_site(monkeypatch, SITE)
    docs = list(LaPhilAdapter().fetch(max_pages=2))
    assert [d.name for d in docs] == ["Ludwig van Beethoven"]
    assert len(fetched) == 2


def test_an_empty_site_yields_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_site(monkeypatch, {}, "<urlset></urlset>")
    assert list(LaPhilAdapter().fetch()) == []


def test_the_walk_follows_the_sitemap_order_not_the_alphabet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sorting the frontier would spend a truncated run on whatever begins with
    "a" — which on this site is a run of old events with no programme left."""
    site = {f"{BASE}/events/{name}": _event() for name in ("zebra", "alpha")}
    sitemap = (
        f"<urlset><url><loc>{BASE}/events/zebra</loc></url><url><loc>{BASE}/events/alpha</loc></url></urlset>"
    )
    fetched = _stub_site(monkeypatch, site, sitemap)
    list(LaPhilAdapter().fetch())
    assert fetched == [f"{BASE}/events/zebra", f"{BASE}/events/alpha"]
