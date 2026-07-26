"""Crawls dashboard views against a stubbed API client — no network access.

The ``AdminAPI`` client methods these views call are covered in test_crawls_api.py.
"""

from __future__ import annotations

from typing import Any

import pytest
import scrapers.crawl_views as crawl_views
from django.test import Client
from scrapers.api import AdminAPIError

SNAPSHOT_PAYLOAD = {
    "source": "archive",
    "id": "2026-07-02T09:52:31-e8533a60",
    "status": "completed",
    "started_at": "2026-07-02T09:52:31+00:00",
    "finished_at": "2026-07-02T10:00:40+00:00",
    "record_count": 42,
    "size_bytes": 1024,
    "error": None,
}

CRAWL_PAYLOAD = {
    "name": "archive",
    "seeds": ["https://example.org/archive"],
    "use_sitemap": True,
    "use_common_crawl": False,
    "allow_patterns": ["*example.org/archive*"],
    "relevance_query": "composer biography",
    "score_threshold": 0.0,
    "follow_links": True,
    "max_depth": 2,
    "max_pages": None,
    "excluded_selector": None,
    "request_delay_s": 0.5,
    "respect_robots": True,
    "editable": True,
    "last_snapshot": SNAPSHOT_PAYLOAD,
}

CODE_CRAWL_PAYLOAD = {
    **CRAWL_PAYLOAD,
    "name": "code-crawl",
    "editable": False,
    "last_snapshot": None,
}


class StubAPI:
    def __init__(self, crawls: list[dict[str, Any]] | None = None, error: str | None = None) -> None:
        self._crawls = crawls or []
        self._error = error
        self.saved: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[str] = []
        self.extracted: list[str] = []
        self.loaded: list[str] = []

    def _maybe_fail(self) -> None:
        if self._error:
            raise AdminAPIError(self._error)

    def list_crawls(self) -> list[dict[str, Any]]:
        self._maybe_fail()
        return self._crawls

    def get_crawl(self, name: str) -> dict[str, Any]:
        self._maybe_fail()
        for crawl in self._crawls:
            if crawl["name"] == name:
                return crawl
        raise AdminAPIError(f"API returned 404: unknown crawl {name!r}")

    def put_crawl(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._maybe_fail()
        self.saved.append((name, payload))
        return {**CRAWL_PAYLOAD, "name": name}

    def delete_crawl(self, name: str) -> None:
        self._maybe_fail()
        self.deleted.append(name)

    def start_crawl(self, name: str) -> dict[str, Any]:
        self._maybe_fail()
        return {"source": name, "snapshot_id": "snap-1", "status": "running"}

    def start_extract(self, name: str) -> dict[str, Any]:
        self._maybe_fail()
        self.extracted.append(name)
        return {"source": name, "snapshot_id": "snap-2", "status": "running"}

    def load_crawl(self, name: str) -> dict[str, Any]:
        self._maybe_fail()
        self.loaded.append(name)
        return {"source": name, "run_id": 7, "status": "running"}


def _install(monkeypatch: pytest.MonkeyPatch, stub: StubAPI) -> None:
    fake = type("FakeAdminAPI", (), {"from_env": classmethod(lambda cls: stub)})
    monkeypatch.setattr(crawl_views, "AdminAPI", fake)


@pytest.fixture
def staff_client(db: None, django_user_model: Any) -> Client:
    user = django_user_model.objects.create_superuser(username="thijs", password="pw")
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_crawls_page_requires_admin_login() -> None:
    response = Client().get("/admin/crawls/")
    assert response.status_code == 302
    assert response["Location"].startswith("/admin/login/")


def test_index_lists_crawls_with_badges_and_actions(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    _install(monkeypatch, StubAPI(crawls=[CRAWL_PAYLOAD, CODE_CRAWL_PAYLOAD]))
    page = staff_client.get("/admin/crawls/").content.decode()
    assert "archive" in page and "code-crawl" in page
    assert page.count('sc-unknown">code<') == 1  # only the code-registered config gets the badge
    assert page.count(">Edit</a>") == 1 and page.count(">Delete</button>") == 1  # code config is read-only
    assert "sc-badge sc-completed" in page
    assert 'http-equiv="refresh"' not in page


def test_index_auto_refreshes_while_a_crawl_is_active(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    running = {**CRAWL_PAYLOAD, "last_snapshot": {**SNAPSHOT_PAYLOAD, "status": "running"}}
    _install(monkeypatch, StubAPI(crawls=[running]))
    page = staff_client.get("/admin/crawls/").content.decode()
    assert '<meta http-equiv="refresh" content="5">' in page


def test_index_shows_error_banner_when_api_unreachable(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    _install(monkeypatch, StubAPI(error="API unreachable at http://localhost:8001"))
    response = staff_client.get("/admin/crawls/")
    assert response.status_code == 200
    assert "API unreachable" in response.content.decode()


def test_new_crawl_form_posts_expected_payload(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    stub = StubAPI()
    _install(monkeypatch, stub)
    response = staff_client.post(
        "/admin/crawls/new/",
        {
            "name": "archive",
            "seeds": "https://example.org/a\n\n  https://example.org/b  \n",
            "use_sitemap": "on",
            "allow_patterns": "*example.org*",
            "relevance_query": "composer works",
            "score_threshold": "0.3",
            "follow_links": "on",
            "max_depth": "1",
            "max_pages": "",
            "excluded_selector": "#cookie-banner",
            "request_delay_s": "1.5",
        },
        follow=True,
    )
    assert response.redirect_chain[0] == ("/admin/crawls/", 302)
    assert "saved crawl archive" in response.content.decode()
    name, payload = stub.saved[0]
    assert name == "archive"
    assert payload == {
        "seeds": ["https://example.org/a", "https://example.org/b"],
        "use_sitemap": True,
        "use_common_crawl": False,  # unchecked checkbox is absent from the POST
        "allow_patterns": ["*example.org*"],
        "relevance_query": "composer works",
        "score_threshold": 0.3,
        "follow_links": True,
        "max_depth": 1,
        "max_pages": None,
        "excluded_selector": "#cookie-banner",
        "request_delay_s": 1.5,
        "respect_robots": False,  # unchecked checkbox is absent from the POST
    }


def test_blank_excluded_selector_posts_null(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    """An empty box means "no extra selectors", not an empty-string selector."""
    stub = StubAPI()
    _install(monkeypatch, stub)
    staff_client.post(
        "/admin/crawls/new/",
        {"name": "archive", "seeds": "https://example.org/a", "excluded_selector": "  "},
    )
    assert stub.saved[0][1]["excluded_selector"] is None


def test_extract_button_starts_a_run(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    stub = StubAPI(crawls=[CRAWL_PAYLOAD])
    _install(monkeypatch, stub)
    response = staff_client.post("/admin/crawls/archive/extract", follow=True)
    assert response.redirect_chain[0] == ("/admin/crawls/", 302)
    assert stub.extracted == ["archive"]
    assert "extracting archive → snapshot snap-2" in response.content.decode()


def test_extract_surfaces_api_error(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    _install(monkeypatch, StubAPI(error="API returned 409: crawl 'archive' has no completed snapshot"))
    response = staff_client.post("/admin/crawls/archive/extract", follow=True)
    assert "no completed snapshot" in response.content.decode()


def test_extract_rejects_get(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    _install(monkeypatch, StubAPI())
    assert staff_client.get("/admin/crawls/archive/extract").status_code == 405


def test_load_button_loads_latest_extract(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    # start_load mirrors start_extract; its AdminAPIError path is covered by the extract twin.
    stub = StubAPI(crawls=[CRAWL_PAYLOAD])
    _install(monkeypatch, stub)
    response = staff_client.post("/admin/crawls/archive/load", follow=True)
    assert response.redirect_chain[0] == ("/admin/crawls/", 302)
    assert stub.loaded == ["archive"]
    assert "loading archive into the database (run 7)" in response.content.decode()
    assert staff_client.get("/admin/crawls/archive/load").status_code == 405  # GET is not allowed


def test_form_keeps_input_and_shows_error_on_bad_number(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    stub = StubAPI()
    _install(monkeypatch, stub)
    response = staff_client.post(
        "/admin/crawls/new/",
        {
            "name": "archive",
            "seeds": "https://example.org/kept-seed-marker",
            "max_depth": "abc",
        },
    )
    assert response.status_code == 200  # re-rendered, not redirected
    page = response.content.decode()
    assert "numeric fields" in page
    assert "kept-seed-marker" in page  # submitted seeds survive the error
    assert stub.saved == []


def test_form_surfaces_api_validation_error(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    _install(monkeypatch, StubAPI(error="API returned 422: follow_links requires at least one allow pattern"))
    response = staff_client.post(
        "/admin/crawls/new/",
        {"name": "bad", "seeds": "https://example.org/", "follow_links": "on"},
    )
    assert response.status_code == 200
    assert "at least one allow pattern" in response.content.decode()


def test_edit_form_prefills_from_api(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    _install(monkeypatch, StubAPI(crawls=[CRAWL_PAYLOAD]))
    page = staff_client.get("/admin/crawls/archive/edit/").content.decode()
    assert ">https://example.org/archive</textarea>" in page  # seeds land in the textarea
    assert 'name="relevance_query" value="composer biography"' in page


def test_edit_code_registered_redirects_with_error(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    _install(monkeypatch, StubAPI(crawls=[CODE_CRAWL_PAYLOAD]))
    response = staff_client.get("/admin/crawls/code-crawl/edit/", follow=True)
    assert response.redirect_chain[0] == ("/admin/crawls/", 302)
    assert "code-registered" in response.content.decode()


def test_delete_crawl_posts_and_redirects(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    stub = StubAPI()
    _install(monkeypatch, stub)
    response = staff_client.post("/admin/crawls/archive/delete", follow=True)
    assert response.redirect_chain[0] == ("/admin/crawls/", 302)
    assert "deleted crawl archive" in response.content.decode()
    assert stub.deleted == ["archive"]
    # GET is not allowed
    assert staff_client.get("/admin/crawls/archive/delete").status_code == 405


def test_start_crawl_redirects_with_message(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    _install(monkeypatch, StubAPI())
    response = staff_client.post("/admin/crawls/archive/crawl", follow=True)
    assert response.redirect_chain[0] == ("/admin/crawls/", 302)
    assert "crawling archive" in response.content.decode()


def test_start_crawl_surfaces_conflict(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    _install(monkeypatch, StubAPI(error="API returned 409: a crawl for 'archive' is already in progress"))
    response = staff_client.post("/admin/crawls/archive/crawl", follow=True)
    assert "already in progress" in response.content.decode()
