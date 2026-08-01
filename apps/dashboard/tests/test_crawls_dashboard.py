"""Crawls dashboard views against a stubbed API client — no network access.

The ``AdminAPI`` client methods these views call are covered in test_crawls_api.py;
the shared stub and payloads live in crawls_stub.py.
"""

from __future__ import annotations

import pytest
from crawls_stub import CODE_CRAWL_PAYLOAD, CRAWL_PAYLOAD, SNAPSHOT_PAYLOAD, StubAPI, install
from django.test import Client


@pytest.mark.django_db
def test_crawls_page_requires_admin_login() -> None:
    response = Client().get("/admin/crawls/")
    assert response.status_code == 302
    assert response["Location"].startswith("/admin/login/")


def test_index_lists_crawls_with_badges_and_actions(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    install(monkeypatch, StubAPI(crawls=[CRAWL_PAYLOAD, CODE_CRAWL_PAYLOAD]))
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
    install(monkeypatch, StubAPI(crawls=[running]))
    page = staff_client.get("/admin/crawls/").content.decode()
    assert '<meta http-equiv="refresh" content="5">' in page


def test_abandon_button_appears_only_for_a_running_snapshot(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    install(monkeypatch, StubAPI(crawls=[CRAWL_PAYLOAD]))
    assert ">Abandon</button>" not in staff_client.get("/admin/crawls/").content.decode()

    running = {**CRAWL_PAYLOAD, "last_snapshot": {**SNAPSHOT_PAYLOAD, "status": "running"}}
    install(monkeypatch, StubAPI(crawls=[running]))
    page = staff_client.get("/admin/crawls/").content.decode()
    assert f'action="/admin/crawls/archive/{SNAPSHOT_PAYLOAD["id"]}/abandon"' in page


def test_abandon_frees_the_crawl_and_reports_what_was_kept(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    stub = StubAPI(crawls=[CRAWL_PAYLOAD])
    install(monkeypatch, stub)
    response = staff_client.post("/admin/crawls/archive/snap-9/abandon", follow=True)
    assert response.redirect_chain[0] == ("/admin/crawls/", 302)
    assert stub.abandoned == [("archive", "snap-9")]
    assert "12 crawled page(s) kept" in response.content.decode()
    assert staff_client.get("/admin/crawls/archive/snap-9/abandon").status_code == 405


def test_abandon_surfaces_api_error(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    install(monkeypatch, StubAPI(error="API returned 409: snapshot archive/snap-9 is not running"))
    response = staff_client.post("/admin/crawls/archive/snap-9/abandon", follow=True)
    assert "is not running" in response.content.decode()


def test_index_shows_error_banner_when_api_unreachable(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    install(monkeypatch, StubAPI(error="API unreachable at http://localhost:8001"))
    response = staff_client.get("/admin/crawls/")
    assert response.status_code == 200
    assert "API unreachable" in response.content.decode()


def test_new_crawl_form_posts_expected_payload(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    stub = StubAPI()
    install(monkeypatch, stub)
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
        "extract_kind": "concerts",  # defaults when the select is absent from the POST
    }


def test_blank_excluded_selector_posts_null(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    """An empty box means "no extra selectors", not an empty-string selector."""
    stub = StubAPI()
    install(monkeypatch, stub)
    staff_client.post(
        "/admin/crawls/new/",
        {"name": "archive", "seeds": "https://example.org/a", "excluded_selector": "  "},
    )
    assert stub.saved[0][1]["excluded_selector"] is None


def test_run_button_chains_the_whole_pipeline(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    # run_crawl_pipeline mirrors start_extract; its AdminAPIError path is covered by the extract twin.
    stub = StubAPI(crawls=[CRAWL_PAYLOAD])
    install(monkeypatch, stub)
    response = staff_client.post("/admin/crawls/archive/run", follow=True)
    assert response.redirect_chain[0] == ("/admin/crawls/", 302)
    assert stub.piped == ["archive"]
    assert "crawl → extract → load" in response.content.decode()
    assert staff_client.get("/admin/crawls/archive/run").status_code == 405  # GET is not allowed


def test_crawls_page_offers_run_alongside_the_single_steps(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    """Run is the one-click path; the steps stay for re-running one on its own."""
    install(monkeypatch, StubAPI(crawls=[CRAWL_PAYLOAD]))
    page = staff_client.get("/admin/crawls/").content.decode()
    for action in ("run", "crawl", "extract", "load"):
        assert f'action="/admin/crawls/archive/{action}"' in page


def test_extract_button_starts_a_run(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    stub = StubAPI(crawls=[CRAWL_PAYLOAD])
    install(monkeypatch, stub)
    response = staff_client.post("/admin/crawls/archive/extract", follow=True)
    assert response.redirect_chain[0] == ("/admin/crawls/", 302)
    assert stub.extracted == ["archive"]
    assert "extracting archive → snapshot snap-2" in response.content.decode()


def test_extract_surfaces_api_error(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    install(monkeypatch, StubAPI(error="API returned 409: crawl 'archive' has no completed snapshot"))
    response = staff_client.post("/admin/crawls/archive/extract", follow=True)
    assert "no completed snapshot" in response.content.decode()


def test_extract_rejects_get(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    install(monkeypatch, StubAPI())
    assert staff_client.get("/admin/crawls/archive/extract").status_code == 405


def test_load_button_loads_latest_extract(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    # start_load mirrors start_extract; its AdminAPIError path is covered by the extract twin.
    stub = StubAPI(crawls=[CRAWL_PAYLOAD])
    install(monkeypatch, stub)
    response = staff_client.post("/admin/crawls/archive/load", follow=True)
    assert response.redirect_chain[0] == ("/admin/crawls/", 302)
    assert stub.loaded == ["archive"]
    assert "loading archive into the database (run 7)" in response.content.decode()
    assert staff_client.get("/admin/crawls/archive/load").status_code == 405  # GET is not allowed


def test_form_keeps_input_and_shows_error_on_bad_number(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    stub = StubAPI()
    install(monkeypatch, stub)
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
    install(monkeypatch, StubAPI(error="API returned 422: follow_links requires at least one allow pattern"))
    response = staff_client.post(
        "/admin/crawls/new/",
        {"name": "bad", "seeds": "https://example.org/", "follow_links": "on"},
    )
    assert response.status_code == 200
    assert "at least one allow pattern" in response.content.decode()


def test_edit_form_prefills_from_api(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    install(monkeypatch, StubAPI(crawls=[CRAWL_PAYLOAD]))
    page = staff_client.get("/admin/crawls/archive/edit/").content.decode()
    assert ">https://example.org/archive</textarea>" in page  # seeds land in the textarea
    assert 'name="relevance_query" value="composer biography"' in page


def test_edit_code_registered_redirects_with_error(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    install(monkeypatch, StubAPI(crawls=[CODE_CRAWL_PAYLOAD]))
    response = staff_client.get("/admin/crawls/code-crawl/edit/", follow=True)
    assert response.redirect_chain[0] == ("/admin/crawls/", 302)
    assert "code-registered" in response.content.decode()


def test_delete_crawl_posts_and_redirects(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    stub = StubAPI()
    install(monkeypatch, stub)
    response = staff_client.post("/admin/crawls/archive/delete", follow=True)
    assert response.redirect_chain[0] == ("/admin/crawls/", 302)
    assert "deleted crawl archive" in response.content.decode()
    assert stub.deleted == ["archive"]
    # GET is not allowed
    assert staff_client.get("/admin/crawls/archive/delete").status_code == 405


def test_start_crawl_redirects_with_message(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    install(monkeypatch, StubAPI())
    response = staff_client.post("/admin/crawls/archive/crawl", follow=True)
    assert response.redirect_chain[0] == ("/admin/crawls/", 302)
    assert "crawling archive" in response.content.decode()


def test_start_crawl_surfaces_conflict(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    install(monkeypatch, StubAPI(error="API returned 409: a crawl for 'archive' is already in progress"))
    response = staff_client.post("/admin/crawls/archive/crawl", follow=True)
    assert "already in progress" in response.content.decode()
