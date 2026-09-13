"""Tests for the harmoniamundi adapter — the sweep and the documents it emits.

The HTTP layer is stubbed at the names ``composer_scrapers.harmoniamundi``
imported, so these exercise the walk and the document shapes without a network.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from composer_scrapers import REGISTRY, EntityDocument, WorkMentionDocument
from composer_scrapers.harmoniamundi import HarmoniaMundiAdapter

BASE = "https://www.harmoniamundi.com"

SITEMAP_INDEX = f"""<sitemapindex>
  <sitemap><loc>{BASE}/albums-sitemap.xml</loc></sitemap>
  <sitemap><loc>{BASE}/artists-sitemap.xml</loc></sitemap>
  <sitemap><loc>{BASE}/composers-sitemap.xml</loc></sitemap>
  <sitemap><loc>{BASE}/concerts-sitemap.xml</loc></sitemap>
</sitemapindex>"""

ALBUM_URLSET = f"""<urlset>
  <url><loc>{BASE}/en/albums/chout/</loc></url>
  <url><loc>{BASE}/en/albums/forthcoming/</loc></url>
  <url><loc>{BASE}/en/albums/gone/</loc></url>
</urlset>"""

ARTIST_URLSET = f"<urlset><url><loc>{BASE}/en/artistes/stanislav-kochanovsky/</loc></url></urlset>"
COMPOSER_URLSET = f"<urlset><url><loc>{BASE}/en/compositeurs/sergey-prokofiev/</loc></url></urlset>"


def _album(
    *, title: str, ref: str = "HMM905406", fmt: str = "1 CD", contents: str = "", extra: str = ""
) -> str:
    return f"""
<meta property="og:image" content="{BASE}/cover.jpg" />
<h1 class="album_title">
  <span class="compositeurs">SERGEI PROKOFIEV</span>{title}
  <span class="artistes as_p">NDR Radiophilharmonie, Stanislav Kochanovsky</span>
</h1>
<div class="feature duration">1h09</div>
<div class="feature cd_number">{fmt}</div>
<div class="feature ref">{ref}</div>
<time datetime="" class="feature release_date">October 2026</time>
<div class="album_humans">
  <div class="humans_list"><h2>Artists</h2><ul>
    <li><div class="human as_h4"><a href="{BASE}/en/artistes/ndr-radiophilharmonie/">NDR
      Radiophilharmonie</a></div><div class="role as_h5">Orchestra</div></li>
    <li><div class="human as_h4"><a href="{BASE}/en/artistes/stanislav-kochanovsky/">Stanislav
      Kochanovsky</a></div><div class="role as_h5">Conductor</div></li>
    <li><div class="human as_h4">Nobuyuki Tsujii</div><div class="role as_h5">Piano</div></li>
    {extra}
  </ul></div>
  <div class="humans_list"><h2>Composers </h2><ul>
    <li><div class="human as_h4"><a href="{BASE}/en/compositeurs/sergey-prokofiev/">Sergey
      Prokofiev</a></div></li>
  </ul></div>
</div>
<div class="harmonia_mundi_description"><h2 class="title">Contents</h2>{contents}</div>
"""


CHOUT = _album(
    title="Chout",
    contents="Sergey Prokofiev (1891-1953)<br>Chout Op. 21<br>· Andantino (6'43)<br>"
    "Symphony No. 1<br>· I. Allegro (4'31)",
)

# Announced but not yet pressed: the feature divs are present and empty.
FORTHCOMING = _album(title="Next season", ref="", fmt="", contents="Some Work<br>· I. Allegro (3'00)")

ARTIST_PAGE = """
<h1 class="humain_title">Stanislav Kochanovsky</h1>
<div class="hero_content as_h4">Conductor</div>
<div id="biographie" class="humain_information biographie"><div class="global_content">
<h2>Biography</h2><p>A Russian conductor.</p></div></div>
"""

COMPOSER_PAGE = """
<h1 class="humain_title">Sergey Prokofiev</h1>
<div class="hero_content as_h4">1891 - 1953</div>
"""

SITE = {
    f"{BASE}/en/albums/chout/": CHOUT,
    f"{BASE}/en/albums/forthcoming/": FORTHCOMING,
    f"{BASE}/en/artistes/stanislav-kochanovsky/": ARTIST_PAGE,
    f"{BASE}/en/compositeurs/sergey-prokofiev/": COMPOSER_PAGE,
    # /en/albums/gone/ is deliberately absent: a stale sitemap entry.
}

SITEMAPS = {
    f"{BASE}/albums-sitemap.xml": ALBUM_URLSET,
    f"{BASE}/artists-sitemap.xml": ARTIST_URLSET,
    f"{BASE}/composers-sitemap.xml": COMPOSER_URLSET,
}


@pytest.fixture
def fetched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub the site, and record the order its pages were read in."""
    seen: list[str] = []

    def fetch_page(_client: Any, url: str, _cache: Any = None) -> str | None:
        seen.append(url)
        return SITE.get(url)

    monkeypatch.setattr("composer_scrapers.harmoniamundi.fetch_sitemap_index", lambda _c: SITEMAP_INDEX)
    monkeypatch.setattr("composer_scrapers.harmoniamundi.fetch_urlset", lambda _c, url: SITEMAPS[url])
    monkeypatch.setattr("composer_scrapers.harmoniamundi.fetch_page", fetch_page)
    monkeypatch.setattr("composer_scrapers.harmoniamundi.make_client", httpx.Client)
    return seen


def _run(max_pages: int | None = None) -> tuple[list[WorkMentionDocument], list[EntityDocument]]:
    mentions: list[WorkMentionDocument] = []
    entities: list[EntityDocument] = []
    for document in HarmoniaMundiAdapter().fetch(max_pages=max_pages):
        if isinstance(document, WorkMentionDocument):
            mentions.append(document)
        else:
            entities.append(document)
    return mentions, entities


# ---- registration ---- #


def test_harmoniamundi_is_registered() -> None:
    adapter = REGISTRY["harmoniamundi"]
    assert isinstance(adapter, HarmoniaMundiAdapter)
    assert adapter.name == "harmoniamundi"
    assert adapter.base_url == "https://www.harmoniamundi.com/en"


# ---- the sweep ---- #


def test_an_unfetchable_album_does_not_end_the_sweep(fetched: list[str]) -> None:
    mentions, _ = _run()
    assert f"{BASE}/en/albums/gone/" in fetched
    assert {mention.raw["album_slug"] for mention in mentions} == {"chout", "forthcoming"}


def test_every_mention_is_yielded_before_any_entity(fetched: list[str]) -> None:
    """A profession is not settled until every credit has been seen."""
    kinds = [type(document).__name__ for document in HarmoniaMundiAdapter().fetch()]
    assert kinds == sorted(kinds, key=lambda name: name != "WorkMentionDocument")


def test_capping_the_albums_still_reads_every_profile(fetched: list[str]) -> None:
    mentions, entities = _run(max_pages=1)
    assert {mention.raw["album_slug"] for mention in mentions} == {"chout"}
    assert f"{BASE}/en/compositeurs/sergey-prokofiev/" in fetched
    assert any(entity.raw["page_kind"] == "composer" for entity in entities)


def test_document_ids_are_unchanged_across_two_runs(fetched: list[str]) -> None:
    """`(source_id, external_id)` is the idempotency key: a shifting id reloads
    every document as a new one each sweep."""
    first_mentions, first_entities = _run()
    second_mentions, second_entities = _run()
    assert [m.id for m in first_mentions] == [m.id for m in second_mentions]
    assert sorted(e.id for e in first_entities) == sorted(e.id for e in second_entities)


# ---- the recording contract ---- #


def test_every_work_on_a_release_shares_one_record_key(fetched: list[str]) -> None:
    """One recording per release, not one per work."""
    mentions, _ = _run()
    chout = [m for m in mentions if m.raw["album_slug"] == "chout"]
    assert len(chout) == 2
    assert {m.raw["record_key"] for m in chout} == {"album:chout"}


def test_the_raw_payload_carries_the_whole_derive_contract(fetched: list[str]) -> None:
    mentions, _ = _run()
    raw = next(m for m in mentions if m.raw["album_slug"] == "chout").raw
    assert raw["_source"] == "scraper"
    assert raw["_kind"] == "recording"
    assert raw["title"] == "Chout"
    assert raw["release_date"] == "2026-10"
    assert raw["label"] == "harmonia mundi"
    assert raw["catalogue_number"] == "HMM905406"
    assert raw["format"] == "1 CD"
    assert raw["url"] == f"{BASE}/en/albums/chout/"


def test_the_mention_title_is_the_work_and_the_raw_title_is_the_release(fetched: list[str]) -> None:
    mentions, _ = _run()
    chout = [m for m in mentions if m.raw["album_slug"] == "chout"]
    assert [m.title for m in chout] == ["Chout Op. 21", "Symphony No. 1"]
    assert {m.raw["title"] for m in chout} == {"Chout"}
    assert {m.composer for m in chout} == {"Sergey Prokofiev"}


def test_a_forthcoming_release_still_becomes_a_recording(fetched: list[str]) -> None:
    """An empty catalogue number must be absent, not empty, or a merge reads it
    as a conflicting one."""
    mentions, _ = _run()
    raw = next(m for m in mentions if m.raw["album_slug"] == "forthcoming").raw
    assert raw["catalogue_number"] is None
    assert raw["format"] is None
    assert raw["record_key"] == "album:forthcoming"


def test_an_album_with_no_tracklist_still_emits_one_mention(fetched: list[str]) -> None:
    """Without it the release yields no mentions, and `derive_recordings` — which
    groups mentions — would produce no recording row for it at all."""
    monkeypatched = dict(SITE)
    monkeypatched[f"{BASE}/en/albums/chout/"] = _album(title="Silent", contents="")
    SITE.update(monkeypatched)
    try:
        mentions, _ = _run()
        silent = [m for m in mentions if m.raw["album_slug"] == "chout"]
        assert len(silent) == 1
        assert silent[0].title == "Silent"
        assert silent[0].composer is None
        assert silent[0].raw["work_source"] == "album"
    finally:
        SITE[f"{BASE}/en/albums/chout/"] = CHOUT


def test_the_verbatim_tracklist_rides_along_with_every_mention(fetched: list[str]) -> None:
    """The parse is a guess; the text it was made from is not."""
    mentions, _ = _run()
    raw = next(m for m in mentions if m.raw["album_slug"] == "chout").raw
    assert raw["work_source"] == "contents"
    assert "Sergey Prokofiev (1891-1953)" in raw["contents"]
    assert [track["title"] for track in raw["tracks"]] == ["Andantino"]


def test_a_mention_carries_its_own_movements_not_the_whole_album(fetched: list[str]) -> None:
    """Repeating the album's list on every work would multiply a 30-work recital."""
    mentions, _ = _run()
    chout = [m for m in mentions if m.raw["album_slug"] == "chout"]
    assert [[t["title"] for t in m.raw["tracks"]] for m in chout] == [["Andantino"], ["I. Allegro"]]


# ---- the participants ---- #


def test_the_artists_list_holds_performers_only(fetched: list[str]) -> None:
    """A composer is not a performer on their own record."""
    mentions, _ = _run()
    raw = next(m for m in mentions if m.raw["album_slug"] == "chout").raw
    assert "Sergey Prokofiev" not in {artist["name"] for artist in raw["artists"]}
    assert raw["composer_credits"] == [{"name": "Sergey Prokofiev", "slug": "sergey-prokofiev"}]


def test_each_participant_carries_a_known_role_and_its_verbatim_discipline(
    fetched: list[str],
) -> None:
    mentions, _ = _run()
    raw = next(m for m in mentions if m.raw["album_slug"] == "chout").raw
    by_name = {artist["name"]: artist for artist in raw["artists"]}
    assert by_name["NDR Radiophilharmonie"]["role"] == "ensemble"
    # "Conductor" says what he is, not what he played, so it is a role and not
    # a discipline — the same split decca's payload makes.
    assert by_name["Stanislav Kochanovsky"] == {
        "name": "Stanislav Kochanovsky",
        "role": "conductor",
        "discipline": None,
    }
    assert by_name["Nobuyuki Tsujii"] == {
        "name": "Nobuyuki Tsujii",
        "role": "soloist",
        "discipline": "Piano",
    }


def test_a_participants_role_and_its_entity_kind_agree(fetched: list[str]) -> None:
    mentions, entities = _run()
    raw = next(m for m in mentions if m.raw["album_slug"] == "chout").raw
    ensembles = {artist["name"] for artist in raw["artists"] if artist["role"] == "ensemble"}
    by_name = {entity.name: entity for entity in entities}
    assert ensembles == {"NDR Radiophilharmonie"}
    for name in ensembles:
        assert by_name[name].kind == "ensemble"


# ---- the people ---- #


def test_a_person_credited_on_two_albums_is_emitted_once(fetched: list[str]) -> None:
    _, entities = _run()
    names = [entity.name for entity in entities]
    assert names.count("Stanislav Kochanovsky") == 1
    assert names.count("Nobuyuki Tsujii") == 1


def test_a_linked_person_is_loaded_under_their_page_and_a_bare_one_under_their_name(
    fetched: list[str],
) -> None:
    _, entities = _run()
    by_name = {entity.name: entity for entity in entities}
    assert by_name["Stanislav Kochanovsky"].id == "/artistes/stanislav-kochanovsky"
    assert by_name["Stanislav Kochanovsky"].url == f"{BASE}/en/artistes/stanislav-kochanovsky/"
    assert by_name["Nobuyuki Tsujii"].id == "/names/Nobuyuki%20Tsujii"
    assert by_name["Nobuyuki Tsujii"].url is None


def test_a_bare_credit_and_a_linked_one_collapse_into_the_linked_person(
    fetched: list[str],
) -> None:
    """The same person is linked on one album and named without a link on another."""
    monkeypatched = _album(
        title="Bare", extra='<li><div class="human as_h4">Stanislav Kochanovsky</div></li>'
    )
    SITE[f"{BASE}/en/albums/forthcoming/"] = monkeypatched
    try:
        _, entities = _run()
        kochanovsky = [e for e in entities if e.name == "Stanislav Kochanovsky"]
        assert len(kochanovsky) == 1
        assert kochanovsky[0].id == "/artistes/stanislav-kochanovsky"
    finally:
        SITE[f"{BASE}/en/albums/forthcoming/"] = FORTHCOMING


def test_a_profile_page_contributes_its_biography_and_life_dates(fetched: list[str]) -> None:
    _, entities = _run()
    by_name = {entity.name: entity for entity in entities}
    assert by_name["Stanislav Kochanovsky"].raw["bio"] == "A Russian conductor."
    prokofiev = by_name["Sergey Prokofiev"]
    claims = {(claim.predicate, claim.object_label or claim.value) for claim in prokofiev.claims}
    assert ("has_profession", "composer") in claims
    assert ("born_on", "1891") in claims
    assert ("died_on", "1953") in claims


def test_a_performing_role_becomes_a_performs_as_claim(fetched: list[str]) -> None:
    _, entities = _run()
    tsujii = next(entity for entity in entities if entity.name == "Nobuyuki Tsujii")
    claims = {(claim.predicate, claim.object_label or claim.value) for claim in tsujii.claims}
    assert claims == {("has_profession", "soloist"), ("performs_as", "Piano")}


def test_an_ensemble_claims_no_profession(fetched: list[str]) -> None:
    """Letting one through as a person would put an orchestra into the person
    dedupe pass."""
    _, entities = _run()
    orchestra = next(entity for entity in entities if entity.name == "NDR Radiophilharmonie")
    assert orchestra.kind == "ensemble"
    assert [claim.predicate for claim in orchestra.claims] == []
