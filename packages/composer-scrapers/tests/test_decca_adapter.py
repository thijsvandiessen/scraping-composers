"""End-to-end tests for the decca adapter, over a fake catalogue.

The HTTP layer is replaced at the names the adapter imported, so these exercise
the whole pass — sitemap to documents — without a socket.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

import pytest
from composer_schema import EntityDocument, WorkMentionDocument
from composer_scrapers import REGISTRY
from composer_scrapers.decca import SITE_URL, DeccaAdapter

_SITEMAP = """<urlset>
  <url><loc>https://www.deccaclassics.com/de/katalog/produkte/verdi-ballo-7</loc></url>
  <url><loc>https://www.deccaclassics.com/de/katalog/produkte/satie-gymnopedies-19</loc></url>
  <url><loc>https://www.deccaclassics.com/en/artists/von-karajan</loc></url>
</urlset>"""


def _artist(artist_id: int, name: str, is_composer: bool = False) -> dict[str, Any]:
    return {"idRaw": artist_id, "screenname": name, "isComposer": is_composer, "urlAlias": None}


def _family(family_id: int, product_id: int, tracks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "idRaw": family_id,
        "headline": f"Release {family_id}",
        "releaseDate": "2006-03-14",
        "products": {
            "edges": [
                {
                    "node": {
                        "idRaw": product_id,
                        "sku": f"SKU-{product_id}",
                        "label": "Decca",
                        "configuration": "CD album",
                        "productCategory": {"name": "CD"},
                        "playlist": {"carriers": [{"name": "CD 1", "tracks": tracks}]},
                    }
                }
            ]
        },
    }


def _track(
    title: str,
    work_id: int,
    work: str,
    contributors: list[dict[str, Any]],
    headings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "titleNumber": 1,
        "title": title,
        "works": {"edges": [{"node": {"idRaw": work_id, "name": work, "level": 1}}]},
        "contributors": contributors,
        "headings": headings or [],
    }


CATALOGUE: dict[int, dict[str, Any]] = {
    7: _family(
        7,
        900,
        [
            _track(
                "Overture",
                449306,
                "Un ballo in maschera",
                [
                    {"function": "Composer", "artist": _artist(1, "Giuseppe Verdi", True)},
                    {"function": "Composer", "artist": _artist(2, "Antonio Somma")},
                    {"function": "Conductor", "artist": _artist(3, "Herbert von Karajan")},
                    {"function": "Orchestra", "artist": _artist(4, "Wiener Philharmoniker")},
                ],
            )
        ],
    ),
    19: _family(
        19,
        901,
        [
            _track(
                "Gymnopédie no. 1",
                5551,
                "Gymnopédies",
                [{"function": "Piano", "artist": _artist(5, "Aldo Ciccolini")}],
                headings=[{"type": "COMPOSER", "name": "Erik Satie (1866 - 1925)"}],
            )
        ],
    ),
}

ROSTER: dict[str, dict[str, Any]] = {
    "von-karajan": {
        "idRaw": 3,
        "screenname": "Herbert von Karajan",
        "name": "Karajan, Herbert von",
        "urlAlias": "von-karajan",
        "isComposer": False,
        "themeType": "contributor",
        "seoDescription": "Austrian conductor.",
    }
}


@pytest.fixture(autouse=True)
def fake_site(monkeypatch: pytest.MonkeyPatch) -> None:
    def fetch_families(_client: Any, ids: Sequence[int], _cache: Any = None) -> Iterator[dict[str, Any]]:
        for family_id in ids:
            if family_id in CATALOGUE:
                yield CATALOGUE[family_id]

    def fetch_artists(_client: Any, slugs: Sequence[str], _cache: Any = None) -> Iterator[dict[str, Any]]:
        for slug in slugs:
            if slug in ROSTER:
                yield ROSTER[slug]

    monkeypatch.setattr("composer_scrapers.decca.fetch_sitemap", lambda _client: _SITEMAP)
    monkeypatch.setattr("composer_scrapers.decca.fetch_families", fetch_families)
    monkeypatch.setattr("composer_scrapers.decca.fetch_artists", fetch_artists)
    monkeypatch.setattr("composer_scrapers.decca.make_client", lambda: _NullClient())


class _NullClient:
    def __enter__(self) -> _NullClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _documents() -> list[EntityDocument | WorkMentionDocument]:
    return list(DeccaAdapter().fetch())


def _mentions() -> list[WorkMentionDocument]:
    return [d for d in _documents() if isinstance(d, WorkMentionDocument)]


def _entities() -> list[EntityDocument]:
    return [d for d in _documents() if isinstance(d, EntityDocument)]


def test_decca_is_registered() -> None:
    assert isinstance(REGISTRY["decca"], DeccaAdapter)
    assert REGISTRY["decca"].name == "decca"
    assert REGISTRY["decca"].base_url == SITE_URL


def test_every_work_becomes_a_mention() -> None:
    assert {m.title for m in _mentions()} == {"Un ballo in maschera", "Gymnopédies"}


def test_a_mention_carries_the_payload_derive_recordings_reads() -> None:
    mention = next(m for m in _mentions() if m.title == "Un ballo in maschera")
    assert mention.raw["_kind"] == "recording"
    assert mention.raw["record_key"] == "product:900"
    assert mention.raw["catalogue_number"] == "SKU-900"
    assert mention.raw["label"] == "Decca"
    assert mention.raw["format"] == "CD album"
    assert mention.raw["release_date"] == "2006-03-14"
    assert {(a["name"], a["role"]) for a in mention.raw["artists"]} == {
        ("Herbert von Karajan", "conductor"),
        ("Wiener Philharmoniker", "ensemble"),
    }


def test_a_mention_points_at_the_english_page() -> None:
    mention = next(m for m in _mentions() if m.title == "Gymnopédies")
    assert mention.url == "https://www.deccaclassics.com/en/catalogue/products/satie-gymnopedies-19"


def test_mention_ids_are_stable_and_unique() -> None:
    mentions = _mentions()
    assert {m.id for m in mentions} == {"/products/900/works/449306", "/products/901/works/5551"}
    assert len({m.id for m in mentions}) == len(mentions)


def test_the_librettist_does_not_become_the_composer() -> None:
    mention = next(m for m in _mentions() if m.title == "Un ballo in maschera")
    assert mention.composer == "Giuseppe Verdi"


def test_a_composer_is_claimed_as_one() -> None:
    verdi = next(e for e in _entities() if e.name == "Giuseppe Verdi")
    assert ("has_profession", "composer") in {(c.predicate, c.object_label) for c in verdi.claims}


def test_life_dates_from_a_heading_become_claims() -> None:
    satie = next(e for e in _entities() if e.name == "Erik Satie")
    assert {(c.predicate, c.value) for c in satie.claims} >= {
        ("born_on", "1866"),
        ("died_on", "1925"),
    }


def test_a_heading_only_composer_gets_a_stable_id() -> None:
    """No artist id to key on, so the name serves — and must not be a salted hash."""
    satie = next(e for e in _entities() if e.name == "Erik Satie")
    assert satie.id == "/names/Erik%20Satie"
    assert satie.raw["artist_id"] is None


def test_a_performer_is_a_soloist_with_their_instrument() -> None:
    pianist = next(e for e in _entities() if e.name == "Aldo Ciccolini")
    claims = {(c.predicate, c.object_label or c.value) for c in pianist.claims}
    assert ("has_profession", "soloist") in claims
    assert ("performs_as", "Piano") in claims


def test_an_ensemble_claims_no_profession() -> None:
    orchestra = next(e for e in _entities() if e.name == "Wiener Philharmoniker")
    assert orchestra.kind == "ensemble"
    assert [c.predicate for c in orchestra.claims if c.predicate == "has_profession"] == []


def test_the_roster_page_enriches_the_artist_the_credits_found() -> None:
    """The same conductor, not a second record: the roster is merged by id."""
    entities = [e for e in _entities() if e.name == "Herbert von Karajan"]
    assert len(entities) == 1
    karajan = entities[0]
    assert karajan.id == "/artists/3"
    assert karajan.raw["bio"] == "Austrian conductor."
    assert karajan.url == "https://www.deccaclassics.com/en/artists/von-karajan"
    assert ("also_known_as", "Karajan, Herbert von") in {(c.predicate, c.value) for c in karajan.claims}


def test_entity_ids_are_unique() -> None:
    entities = _entities()
    assert len({e.id for e in entities}) == len(entities)


def test_max_pages_caps_the_families_read() -> None:
    documents = list(DeccaAdapter().fetch(max_pages=1))
    assert {m.title for m in documents if isinstance(m, WorkMentionDocument)} == {"Un ballo in maschera"}


def test_the_roster_is_read_in_full_even_by_a_capped_run() -> None:
    documents = list(DeccaAdapter().fetch(max_pages=1))
    assert any(isinstance(d, EntityDocument) and d.raw["bio"] for d in documents)
