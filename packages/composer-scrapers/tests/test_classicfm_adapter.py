"""Tests for the classicfm adapter's document output."""

from __future__ import annotations

import pytest
from composer_scrapers import REGISTRY
from composer_scrapers.classicfm import ClassicFmAdapter

COMPOSERS_HTML = """
<div class="grouped_links__groups">
    <div class="grouped_links__group">
        <ul class="grouped_links__list">
            <li><a href="/composers/bach/" class="grouped_links__list__link">Bach</a></li>
            <li><a href="/composers/mozart/" class="grouped_links__list__link">Mozart</a></li>
        </ul>
    </div>
</div>
"""

ARTISTS_HTML = """
<div class="grouped_links__groups">
    <div class="grouped_links__group">
        <ul class="grouped_links__list">
            <li><a href="/artists/lang-lang/" class="grouped_links__list__link">Lang Lang</a></li>
        </ul>
    </div>
</div>
"""


def _stub_pages(monkeypatch: pytest.MonkeyPatch, composers_html: str = "", artists_html: str = "") -> None:
    monkeypatch.setattr(
        "composer_scrapers.classicfm.fetch_index_pages",
        lambda: (composers_html, artists_html),
    )


def test_classicfm_is_registered() -> None:
    assert isinstance(REGISTRY["classicfm"], ClassicFmAdapter)
    assert REGISTRY["classicfm"].name == "classicfm"


def test_fetch_yields_one_entity_per_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pages(monkeypatch, COMPOSERS_HTML, ARTISTS_HTML)
    docs = list(ClassicFmAdapter().fetch())
    assert [d.name for d in docs] == ["Bach", "Mozart", "Lang Lang"]


def test_entity_id_and_url_derive_from_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pages(monkeypatch, COMPOSERS_HTML, "")
    (bach, _mozart) = list(ClassicFmAdapter().fetch())
    assert bach.id == "/composers/bach/"
    assert bach.url == "https://www.classicfm.com/composers/bach/"
    assert bach.source_name == "classicfm"
    assert bach.kind == "person"


def test_composer_entries_get_has_profession_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pages(monkeypatch, COMPOSERS_HTML, "")
    (bach, _mozart) = list(ClassicFmAdapter().fetch())
    assert len(bach.claims) == 1
    claim = bach.claims[0]
    assert claim.predicate == "has_profession"
    assert claim.object_kind == "profession"
    assert claim.object_label == "composer"


def test_artist_entries_get_no_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pages(monkeypatch, "", ARTISTS_HTML)
    (lang_lang,) = list(ClassicFmAdapter().fetch())
    assert lang_lang.claims == ()


def test_fetch_passes_max_pages_through_as_a_cap_on_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pages(monkeypatch, COMPOSERS_HTML, ARTISTS_HTML)
    docs = list(ClassicFmAdapter().fetch(max_pages=2))
    assert [d.name for d in docs] == ["Bach", "Mozart"]


def test_fetch_with_no_entries_yields_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pages(monkeypatch, "", "")
    assert list(ClassicFmAdapter().fetch()) == []
