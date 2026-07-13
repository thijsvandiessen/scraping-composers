"""Dashboard tests: the admin-API client against a mock transport, and the
views against a stubbed client — no network access."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import scrapers.views as views
from django.test import Client
from scrapers.api import AdminAPI, AdminAPIError, DataAPI

# ---------------------------------------------------------------------------
# AdminAPI client
# ---------------------------------------------------------------------------

SNAPSHOT_PAYLOAD = {
    "source": "wikidata",
    "id": "2026-07-02T09:52:31-e8533a60",
    "status": "completed",
    "started_at": "2026-07-02T09:52:31+00:00",
    "finished_at": "2026-07-02T11:00:40+00:00",
    "record_count": 59301,
    "size_bytes": 80_000_000,
    "error": None,
}

SCRAPERS_PAYLOAD = [
    {
        "name": "imslp",
        "base_url": "https://imslp.org",
        "cadence": "monthly",
        "due": True,
        "last_snapshot": None,
    },
    {
        "name": "wikidata",
        "base_url": "https://www.wikidata.org",
        "cadence": "monthly",
        "due": False,
        "last_snapshot": SNAPSHOT_PAYLOAD,
    },
]


def _api(handler: Any) -> AdminAPI:
    return AdminAPI(base_url="http://testserver", transport=httpx.MockTransport(handler))


def test_client_list_scrapers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/admin/v1/scrapers"
        return httpx.Response(200, json=SCRAPERS_PAYLOAD)

    assert _api(handler).list_scrapers() == SCRAPERS_PAYLOAD


def test_client_fetch_scraper_posts_to_named_scraper() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("POST", "/admin/v1/scrapers/imslp/fetch")
        return httpx.Response(202, json={"source": "imslp", "snapshot_id": "snap-1", "status": "running"})

    assert _api(handler).fetch_scraper("imslp")["snapshot_id"] == "snap-1"


def test_client_snapshots_process_and_runs() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/v1/scrapers/fetch-due":
            return httpx.Response(200, json=[{"source": "imslp", "snapshot_id": "s", "status": "running"}])
        if request.url.path == "/admin/v1/snapshots":
            return httpx.Response(200, json=[SNAPSHOT_PAYLOAD])
        if request.url.path.endswith("/process"):
            assert request.url.path == "/admin/v1/snapshots/wikidata/snap-1/process"
            return httpx.Response(202, json={"run_id": 15, "source": "wikidata", "status": "running"})
        assert request.url.path == "/admin/v1/runs"
        assert request.url.params["limit"] == "5"
        return httpx.Response(200, json=[])

    api = _api(handler)
    assert api.fetch_due()[0]["source"] == "imslp"
    assert api.list_snapshots() == [SNAPSHOT_PAYLOAD]
    assert api.process_snapshot("wikidata", "snap-1")["run_id"] == 15
    assert api.list_runs(limit=5) == []


def test_client_surfaces_api_error_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "a fetch for 'imslp' is already in progress"})

    with pytest.raises(AdminAPIError, match="already in progress"):
        _api(handler).fetch_scraper("imslp")


def test_client_wraps_connection_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(AdminAPIError, match="unreachable at http://testserver"):
        _api(handler).list_scrapers()


def test_client_sends_admin_key_header_when_set() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Admin-Key"] == "secret"
        return httpx.Response(200, json=[])

    api = AdminAPI(base_url="http://testserver", api_key="secret", transport=httpx.MockTransport(handler))
    assert api.list_scrapers() == []


# ---------------------------------------------------------------------------
# Views (stubbed client — the views only ever talk to AdminAPI)
# ---------------------------------------------------------------------------


class StubAPI:
    def __init__(
        self,
        scrapers: list[dict[str, Any]] | None = None,
        snapshots: list[dict[str, Any]] | None = None,
        runs: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ) -> None:
        self._scrapers = scrapers or []
        self._snapshots = snapshots or []
        self._runs = runs or []
        self._error = error

    def _maybe_fail(self) -> None:
        if self._error:
            raise AdminAPIError(self._error)

    def list_scrapers(self) -> list[dict[str, Any]]:
        self._maybe_fail()
        return self._scrapers

    def list_snapshots(self) -> list[dict[str, Any]]:
        self._maybe_fail()
        return self._snapshots

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        self._maybe_fail()
        return self._runs

    def fetch_scraper(self, name: str) -> dict[str, Any]:
        self._maybe_fail()
        return {"source": name, "snapshot_id": "snap-1", "status": "running"}

    def fetch_due(self) -> list[dict[str, Any]]:
        self._maybe_fail()
        return [{"source": "imslp", "snapshot_id": "snap-2", "status": "running"}]

    def process_snapshot(self, source: str, snapshot_id: str) -> dict[str, Any]:
        self._maybe_fail()
        return {"run_id": 12, "source": source, "status": "running"}

    def gold_status(self) -> dict[str, Any]:
        self._maybe_fail()
        return {
            "exists": True,
            "status": "completed",
            "started_at": "2026-07-02T15:00:00+00:00",
            "finished_at": "2026-07-02T15:00:40+00:00",
            "error": None,
            "stats": {"persons_kept": 15387, "persons_dropped": 130192},
        }

    def start_promote(self, options: dict[str, Any] | None = None) -> dict[str, Any]:
        self._maybe_fail()
        self.promote_options = options
        return {
            "exists": True,
            "status": "running",
            "started_at": None,
            "finished_at": None,
            "error": None,
            "stats": {},
        }


def _install(monkeypatch: pytest.MonkeyPatch, stub: StubAPI) -> None:
    fake = type("FakeAdminAPI", (), {"from_env": classmethod(lambda cls: stub)})
    monkeypatch.setattr(views, "AdminAPI", fake)


@pytest.fixture
def staff_client(db: None, django_user_model: Any) -> Client:
    """A test client logged in to the Unfold admin as a superuser."""
    user = django_user_model.objects.create_superuser(username="thijs", password="pw")
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_pages_require_admin_login() -> None:
    for url in ("/admin/scrapers/", "/admin/load/"):
        response = Client().get(url)
        assert response.status_code == 302
        assert response["Location"].startswith("/admin/login/")


def test_index_lists_scrapers_with_due_badge(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    _install(monkeypatch, StubAPI(scrapers=SCRAPERS_PAYLOAD))
    response = staff_client.get("/admin/scrapers/")
    assert response.status_code == 200
    page = response.content.decode()
    assert "imslp" in page and "wikidata" in page
    assert "sc-badge sc-due" in page
    assert "59301" in page  # last-snapshot record count rendered
    assert 'http-equiv="refresh"' not in page  # nothing running -> no auto-refresh


def test_index_shows_error_banner_when_api_unreachable(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    _install(monkeypatch, StubAPI(error="admin API unreachable at http://localhost:8001"))
    response = staff_client.get("/admin/scrapers/")
    assert response.status_code == 200  # page still renders
    assert "admin API unreachable" in response.content.decode()


def test_index_auto_refreshes_while_a_fetch_is_active(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    running = {**SNAPSHOT_PAYLOAD, "status": "running"}
    scrapers = [{**SCRAPERS_PAYLOAD[1], "last_snapshot": running}]
    _install(monkeypatch, StubAPI(scrapers=scrapers))
    page = staff_client.get("/admin/scrapers/").content.decode()
    assert '<meta http-equiv="refresh" content="5">' in page


def test_start_fetch_redirects_with_message(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    _install(monkeypatch, StubAPI())
    response = staff_client.post("/admin/scrapers/imslp/fetch", follow=True)
    assert response.redirect_chain[0] == ("/admin/scrapers/", 302)
    assert "fetching imslp" in response.content.decode()


def test_start_fetch_surfaces_conflict_message(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    _install(monkeypatch, StubAPI(error="admin API returned 409: a fetch for 'imslp' is already in progress"))
    response = staff_client.post("/admin/scrapers/imslp/fetch", follow=True)
    assert "already in progress" in response.content.decode()


def test_fetch_due_reports_started_sources(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    _install(monkeypatch, StubAPI())
    response = staff_client.post("/admin/scrapers/fetch-due", follow=True)
    assert "started 1 fetch(es): imslp" in response.content.decode()


def test_load_page_lists_snapshots_with_load_button(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    failed = {**SNAPSHOT_PAYLOAD, "id": "snap-bad", "status": "failed", "error": "boom"}
    _install(monkeypatch, StubAPI(snapshots=[SNAPSHOT_PAYLOAD, failed]))
    page = staff_client.get("/admin/load/").content.decode()
    assert SNAPSHOT_PAYLOAD["id"] in page
    assert page.count("Load into DB") == 1  # only the completed snapshot is loadable
    assert "boom" in page  # failed snapshot's error shown


def test_process_snapshot_redirects_with_message(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    _install(monkeypatch, StubAPI())
    response = staff_client.post("/admin/load/wikidata/snap-1/process", follow=True)
    assert response.redirect_chain[0] == ("/admin/load/", 302)
    assert "loading snapshot snap-1" in response.content.decode()


def test_load_page_auto_refreshes_while_a_run_is_active(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    run: dict[str, Any] = {
        "id": 8,
        "source": "imslp",
        "status": "running",
        "started_at": "2026-07-02T12:00:00",
        "finished_at": None,
        "records_seen": 0,
        "records_new": 0,
        "error": None,
    }
    _install(monkeypatch, StubAPI(runs=[run]))
    page = staff_client.get("/admin/load/").content.decode()
    assert '<meta http-equiv="refresh" content="5">' in page


def test_post_endpoints_reject_get(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    _install(monkeypatch, StubAPI())
    assert staff_client.get("/admin/scrapers/imslp/fetch").status_code == 405
    assert staff_client.get("/admin/scrapers/fetch-due").status_code == 405
    assert staff_client.get("/admin/load/wikidata/snap-1/process").status_code == 405


# ---------------------------------------------------------------------------
# DataAPI client (consumer API)
# ---------------------------------------------------------------------------

ENTITY_ID = "7f9d3c1e-0000-0000-0000-000000000001"

STATS_PAYLOAD: dict[str, Any] = {
    "entities_total": 10,
    "entities_by_kind": {"person": 6, "profession": 3, "place": 1},
    "claims": 20,
    "records": 12,
    "records_by_source": {"imslp": 8, "wikidata": 4},
    "works": 2,
    "work_titles": 3,
    "work_mentions": 5,
    "mentions_by_status": {"auto_matched": 4, "created": 1},
    "persons_linked": 1,
    "person_matches_to_review": 2,
}

ENTITY_DETAIL_PAYLOAD: dict[str, Any] = {
    "id": ENTITY_ID,
    "label": "Bach, Johann Sebastian",
    "kind": "person",
    "created_at": "2026-06-11T10:00:00",
    "canonical_entity_id": None,
    "claims": [
        {
            "predicate": "born_on",
            "value": "1685-03-21",
            "object_label": None,
            "object_id": None,
            "source": "wikidata",
            "source_url": "https://www.wikidata.org",
        },
        {
            "predicate": "has_profession",
            "value": None,
            "object_label": "composer",
            "object_id": "7f9d3c1e-0000-0000-0000-000000000002",
            "source": "imslp",
            "source_url": None,
        },
    ],
    "incoming_total": 1,
    "incoming": [
        {
            "subject_id": "7f9d3c1e-0000-0000-0000-000000000003",
            "subject_label": "Symphony No. 5",
            "predicate": "composed_by",
            "source": "nyphil",
        }
    ],
}

WORKS_PAYLOAD: dict[str, Any] = {
    "items": [
        {
            "id": "7f9d3c1e-0000-0000-0000-000000000004",
            "canonical_title": "Symphony No. 5, Op. 67",
            "composer_id": ENTITY_ID,
            "composer_label": "Beethoven, Ludwig van",
            "work_type": "symphony",
            "opus_number": "67",
            "catalogue": None,
            "musical_key": "C minor",
            "number": 5,
            "mention_count": 12,
            "aliases": ["Sinfonie Nr. 5 c-moll, op. 67"],
        }
    ],
    "total": 1,
    "page": 1,
    "limit": 20,
}


def _data_api(handler: Any) -> DataAPI:
    return DataAPI(base_url="http://testserver", transport=httpx.MockTransport(handler))


def test_data_client_endpoints_and_params() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/stats":
            return httpx.Response(200, json=STATS_PAYLOAD)
        if request.url.path == "/v1/entities":
            assert dict(request.url.params) == {
                "page": "2",
                "limit": "20",
                "q": "bach",
                "kind": "person",
                "order": "label",
            }
            return httpx.Response(200, json={"items": [], "total": 0, "page": 2, "limit": 20})
        if request.url.path == f"/v1/entities/{ENTITY_ID}":
            return httpx.Response(200, json=ENTITY_DETAIL_PAYLOAD)
        assert request.url.path == "/v1/works"
        assert request.url.params["q"] == "symphony"
        return httpx.Response(200, json=WORKS_PAYLOAD)

    api = _data_api(handler)
    assert api.stats() == STATS_PAYLOAD
    assert api.list_entities(q="bach", kind="person", page=2)["total"] == 0
    assert api.get_entity(ENTITY_ID)["label"] == "Bach, Johann Sebastian"
    assert api.list_works(q="symphony")["items"][0]["mention_count"] == 12


def test_data_client_wraps_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "entity not found"})

    with pytest.raises(AdminAPIError, match="entity not found"):
        _data_api(handler).get_entity(ENTITY_ID)


# ---------------------------------------------------------------------------
# Data views (stubbed DataAPI)
# ---------------------------------------------------------------------------


class StubDataAPI:
    def __init__(self, error: str | None = None) -> None:
        self._error = error

    def _maybe_fail(self) -> None:
        if self._error:
            raise AdminAPIError(self._error)

    def stats(self) -> dict[str, Any]:
        self._maybe_fail()
        return STATS_PAYLOAD

    def list_entities(
        self,
        q: str | None = None,
        kind: str | None = None,
        page: int = 1,
        limit: int = 20,
        order: str = "label",
    ) -> dict[str, Any]:
        self._maybe_fail()
        items = [
            {"id": ENTITY_ID, "label": "Bach, Johann Sebastian", "kind": "person", "created_at": "2026-06-11"}
        ]
        return {"items": items, "total": 45, "page": page, "limit": limit}

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        self._maybe_fail()
        return ENTITY_DETAIL_PAYLOAD

    def list_people(
        self, role: str, q: str | None = None, page: int = 1, limit: int = 20, sort: str = "label"
    ) -> dict[str, Any]:
        self._maybe_fail()
        items = [
            {
                "id": ENTITY_ID,
                "label": "Bach, Johann Sebastian",
                "created_at": "2026-06-11",
                "concert_count": 27,
            }
        ]
        return {"items": items, "total": 1, "page": page, "limit": limit}

    def person_concerts(self, person_id: str, page: int = 1, limit: int = 20) -> dict[str, Any]:
        self._maybe_fail()
        items = [
            {
                "id": 1,
                "source": "berlinphil",
                "date": "1985-03-01",
                "venue": None,
                "url": "https://dch.example/1",
                "role": "conductor",
                "works": ["Ein Heldenleben"],
            },
            {
                "id": 2,
                "source": "concertgebouw_archive",
                "date": "1929-06-30",
                "venue": "Amsterdam",
                "url": None,
                "role": "conductor",
                "works": ["Symfonie nr. 5", "Egmont Ouverture"],
            },
        ]
        return {
            "person_id": person_id,
            "person_label": "Bach, Johann Sebastian",
            "items": items,
            "total": 2,
            "page": page,
            "limit": limit,
        }

    def list_works(self, q: str | None = None, page: int = 1, limit: int = 20) -> dict[str, Any]:
        self._maybe_fail()
        return WORKS_PAYLOAD

    def list_concerts(
        self, q: str | None = None, source: str | None = None, page: int = 1, limit: int = 20
    ) -> dict[str, Any]:
        self._maybe_fail()
        items = [
            {
                "id": 7,
                "source": "nyphil",
                "date": "1989-10-31",
                "venue": "Avery Fisher Hall, Manhattan, NY",
                "season": "1989-90",
                "event_type": "Subscription Season",
                "url": None,
                "conductors": ["Bernstein, Leonard"],
                "soloist_count": 2,
                "work_count": 3,
            }
        ]
        return {"items": items, "total": 1, "page": page, "limit": limit}

    def get_concert(self, concert_id: int) -> dict[str, Any]:
        self._maybe_fail()
        return {
            "id": concert_id,
            "source": "nyphil",
            "date": "1989-10-31",
            "venue": "Avery Fisher Hall, Manhattan, NY",
            "season": "1989-90",
            "event_type": "Subscription Season",
            "url": None,
            "participants": [
                {
                    "role": "conductor",
                    "name": "Bernstein, Leonard",
                    "discipline": None,
                    "entity_id": ENTITY_ID,
                },
                {"role": "soloist", "name": "Pavarotti, Luciano", "discipline": "Tenor", "entity_id": None},
            ],
            "works": [{"title": "LUISA MILLER", "composer": "Verdi, Giuseppe"}],
        }

    def list_mentions(self, status: str | None = None, page: int = 1, limit: int = 20) -> dict[str, Any]:
        self._maybe_fail()
        items = [
            {
                "id": 42,
                "source": "nyphil",
                "composer": "Mahler, Gustav",
                "title": "Songs of a Traveller",
                "status": status or "needs_review",
                "score": 0.82,
                "method": "title_similarity",
                "work_id": None,
                "work_title": None,
                "candidate_work_id": "7f9d3c1e-0000-0000-0000-000000000005",
                "candidate_title": "Songs of a Wayfarer",
            }
        ]
        return {"items": items, "total": 1, "page": page, "limit": limit}


def _install_data(monkeypatch: pytest.MonkeyPatch, stub: StubDataAPI) -> None:
    fake = type(
        "FakeDataAPI",
        (),
        {"gold": classmethod(lambda cls: stub), "silver": classmethod(lambda cls: stub)},
    )
    monkeypatch.setattr(views, "DataAPI", fake)


@pytest.mark.django_db
def test_data_pages_require_admin_login() -> None:
    for url in (
        "/admin/data/",
        "/admin/data/entities/",
        f"/admin/data/entities/{ENTITY_ID}/",
        "/admin/data/works/",
    ):
        response = Client().get(url)
        assert response.status_code == 302
        assert response["Location"].startswith("/admin/login/")


def test_data_overview_renders_counts(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    _install_data(monkeypatch, StubDataAPI())
    page = staff_client.get("/admin/data/").content.decode()
    assert "person" in page and "profession" in page
    assert "wikidata" in page
    assert "auto_matched" in page


def test_entities_page_renders_rows_and_pagination(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    _install_data(monkeypatch, StubDataAPI())
    page = staff_client.get("/admin/data/entities/?q=bach&page=2").content.decode()
    assert "Bach, Johann Sebastian" in page
    assert f"/admin/data/entities/{ENTITY_ID}/" in page  # row links to detail
    assert "page 2 of 3" in page  # 45 total / 20 per page
    assert "q=bach" in page and "page=1" in page  # prev link keeps the search
    assert "page=3" in page  # next link
    # arrow keys page through results: the handler targets the prev/next links
    assert 'id="sc-prev"' in page and 'id="sc-next"' in page
    assert "ArrowRight" in page and "ArrowLeft" in page


def test_entity_detail_renders_claims_and_links(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    _install_data(monkeypatch, StubDataAPI())
    page = staff_client.get(f"/admin/data/entities/{ENTITY_ID}/").content.decode()
    assert "born_on" in page and "1685-03-21" in page
    assert "/admin/data/entities/7f9d3c1e-0000-0000-0000-000000000002/" in page  # claim object link
    assert "Symphony No. 5" in page  # incoming claim
    assert "composed_by" in page


def test_works_page_renders_aliases_and_composer_link(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    _install_data(monkeypatch, StubDataAPI())
    page = staff_client.get("/admin/data/works/").content.decode()
    assert "Symphony No. 5, Op. 67" in page
    assert "Sinfonie Nr. 5 c-moll, op. 67" in page
    assert f"/admin/data/entities/{ENTITY_ID}/" in page  # composer links to entity
    assert "opus=67" in page


def test_entity_kind_page_shows_tabs_and_random_sample(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    _install_data(monkeypatch, StubDataAPI())
    page = staff_client.get("/admin/data/entities/person/").content.decode()
    # tabs for every kind from stats, with the current one active
    assert 'href="/admin/data/entities/place/"' in page
    assert "person · 6" in page and "place · 1" in page
    assert page.count("sc-tab sc-tab-active") == 1  # exactly one active tab
    assert "?order=random" in page  # spot-check button


def test_entity_kind_page_random_order_forwarded(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    calls: list[str] = []

    class RecordingStub(StubDataAPI):
        def list_entities(
            self,
            q: str | None = None,
            kind: str | None = None,
            page: int = 1,
            limit: int = 20,
            order: str = "label",
        ) -> dict[str, Any]:
            calls.append(f"{kind}:{order}")
            return super().list_entities(q=q, kind=kind, page=page, limit=limit)

    _install_data(monkeypatch, RecordingStub())
    staff_client.get("/admin/data/entities/place/?order=random")
    assert calls == ["place:random"]


def test_people_pages_render_per_role(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    _install_data(monkeypatch, StubDataAPI())
    for role in ("composers", "soloists", "conductors"):
        page = staff_client.get(f"/admin/data/people/{role}/").content.decode()
        assert "Bach, Johann Sebastian" in page
        assert f"/admin/data/entities/{ENTITY_ID}/" in page  # row links to entity detail
        assert page.count("sc-tab sc-tab-active") == 1  # role tabs with the current one active


def test_people_page_unknown_role_404(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    _install_data(monkeypatch, StubDataAPI())
    assert staff_client.get("/admin/data/people/violinists/").status_code == 404


def test_data_client_list_people_hits_role_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/soloists"
        assert request.url.params["q"] == "doe"
        return httpx.Response(200, json={"items": [], "total": 0, "page": 1, "limit": 20})

    assert _data_api(handler).list_people("soloists", q="doe")["total"] == 0


def test_people_page_shows_concert_counts_and_sort_toggle(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    _install_data(monkeypatch, StubDataAPI())
    page = staff_client.get("/admin/data/people/conductors/").content.decode()
    assert f"/admin/data/people/conductors/{ENTITY_ID}/concerts" in page  # count links to concerts
    assert ">27<" in page
    assert "sort=concerts" in page  # toggle offered


def test_people_page_forwards_concert_sort(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    calls: list[str] = []

    class RecordingStub(StubDataAPI):
        def list_people(
            self, role: str, q: str | None = None, page: int = 1, limit: int = 20, sort: str = "label"
        ) -> dict[str, Any]:
            calls.append(f"{role}:{sort}")
            return super().list_people(role, q=q, page=page, limit=limit, sort=sort)

    _install_data(monkeypatch, RecordingStub())
    staff_client.get("/admin/data/people/conductors/?sort=concerts")
    assert calls == ["conductors:concerts"]


def test_person_concerts_page_renders(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    _install_data(monkeypatch, StubDataAPI())
    page = staff_client.get(f"/admin/data/people/conductors/{ENTITY_ID}/concerts").content.decode()
    assert "Bach, Johann Sebastian" in page
    assert "1985-03-01" in page and "Amsterdam" in page
    assert "Ein Heldenleben" in page and "Egmont Ouverture" in page
    assert 'href="https://dch.example/1"' in page  # source links out when a url exists


def test_entity_detail_links_to_person_concerts(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    _install_data(monkeypatch, StubDataAPI())
    page = staff_client.get(f"/admin/data/entities/{ENTITY_ID}/").content.decode()
    assert f"/admin/data/people/{ENTITY_ID}/concerts" in page
    assert "concerts by this person (2)" in page  # total from the gold stub


def test_entity_detail_no_concerts_link_for_places(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    class PlaceStub(StubDataAPI):
        def get_entity(self, entity_id: str) -> dict[str, Any]:
            return {**ENTITY_DETAIL_PAYLOAD, "kind": "place"}

    _install_data(monkeypatch, PlaceStub())
    page = staff_client.get(f"/admin/data/entities/{ENTITY_ID}/").content.decode()
    assert "concerts by this person" not in page


def test_entity_detail_renders_when_gold_is_down(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    class GoldDownStub(StubDataAPI):
        def person_concerts(self, person_id: str, page: int = 1, limit: int = 20) -> dict[str, Any]:
            raise AdminAPIError("API unreachable at http://localhost:8000")

    _install_data(monkeypatch, GoldDownStub())
    response = staff_client.get(f"/admin/data/entities/{ENTITY_ID}/")
    assert response.status_code == 200
    page = response.content.decode()
    assert "born_on" in page  # silver content still there
    assert "concerts by this person" in page  # link offered, just without a count
    assert "concerts by this person (" not in page


def test_person_concerts_reachable_without_role(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    _install_data(monkeypatch, StubDataAPI())
    page = staff_client.get(f"/admin/data/people/{ENTITY_ID}/concerts").content.decode()
    assert "Bach, Johann Sebastian" in page
    assert "← entity" in page  # back-link goes to the entity page, not a role page
    assert f"/admin/data/entities/{ENTITY_ID}/" in page


def test_concerts_list_page_renders(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    _install_data(monkeypatch, StubDataAPI())
    page = staff_client.get("/admin/data/concerts/").content.decode()
    assert "1989-10-31" in page and "Avery Fisher Hall" in page
    assert "Bernstein, Leonard" in page
    assert "/admin/data/concerts/7/" in page  # row links to detail


def test_concert_detail_page_renders(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    _install_data(monkeypatch, StubDataAPI())
    page = staff_client.get("/admin/data/concerts/7/").content.decode()
    assert "Avery Fisher Hall" in page and "1989-90" in page
    assert f"/admin/data/entities/{ENTITY_ID}/" in page  # resolved participant links to entity
    assert "Pavarotti, Luciano" in page and "Tenor" in page  # unresolved shown as plain text
    assert "LUISA MILLER" in page and "Verdi, Giuseppe" in page  # programme


@pytest.mark.django_db
def test_concert_pages_require_login() -> None:
    for url in ("/admin/data/concerts/", "/admin/data/concerts/7/"):
        response = Client().get(url)
        assert response.status_code == 302
        assert response["Location"].startswith("/admin/login/")


def test_promote_page_shows_gold_status(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    _install(monkeypatch, StubAPI())
    page = staff_client.get("/admin/promote/").content.decode()
    assert "Promote silver" in page
    assert "sc-badge sc-completed" in page  # gold present + last promote completed
    assert "persons_kept" in page and "15387" in page


def test_promote_button_posts_and_redirects(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    stub = StubAPI()
    _install(monkeypatch, stub)
    response = staff_client.post("/admin/promote/start", follow=True)
    assert response.redirect_chain[0] == ("/admin/promote/", 302)
    assert "rebuilding the gold database" in response.content.decode()
    assert stub.promote_options is None  # bare POST stays an all-default run
    assert staff_client.get("/admin/promote/start").status_code == 405


def test_promote_page_renders_config_fields(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    _install(monkeypatch, StubAPI())
    page = staff_client.get("/admin/promote/").content.decode()
    for field in ("drop_unevidenced_persons", "collapse_duplicates", "prune_unreferenced"):
        assert f'name="{field}" checked' in page
    assert 'name="min_sitelinks"' in page
    assert 'name="gold_path"' in page


def test_promote_form_passes_options_through(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    stub = StubAPI()
    _install(monkeypatch, stub)
    # rule 3 unchecked (absent), the others checked; a threshold and custom path set
    response = staff_client.post(
        "/admin/promote/start",
        {
            "options_form": "1",
            "drop_unevidenced_persons": "on",
            "collapse_duplicates": "on",
            "min_sitelinks": "150",
            "gold_path": "/data/gold-alt.db",
        },
        follow=True,
    )
    assert "rebuilding the gold database" in response.content.decode()
    assert stub.promote_options == {
        "prune_unreferenced": False,
        "min_sitelinks": 150,
        "gold_path": "/data/gold-alt.db",
    }


def test_promote_form_defaults_send_no_options(monkeypatch: pytest.MonkeyPatch, staff_client: Client) -> None:
    stub = StubAPI()
    _install(monkeypatch, stub)
    fields = {
        "options_form": "1",
        "drop_unevidenced_persons": "on",
        "collapse_duplicates": "on",
        "prune_unreferenced": "on",
        "min_sitelinks": "",
        "gold_path": "",
    }
    staff_client.post("/admin/promote/start", fields, follow=True)
    assert stub.promote_options is None  # untouched form: bodiless POST, server defaults


def test_promote_form_rejects_non_numeric_sitelinks(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    stub = StubAPI()
    _install(monkeypatch, stub)
    response = staff_client.post(
        "/admin/promote/start", {"options_form": "1", "min_sitelinks": "many"}, follow=True
    )
    assert response.redirect_chain[0] == ("/admin/promote/", 302)
    assert "min sitelinks must be a whole number" in response.content.decode()
    assert not hasattr(stub, "promote_options")  # the API was never called


def test_review_page_lists_needs_review_mentions(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    _install_data(monkeypatch, StubDataAPI())
    page = staff_client.get("/admin/data/review/").content.decode()
    assert "Songs of a Traveller" in page  # the queued mention
    assert "Songs of a Wayfarer" in page  # its best candidate
    assert "0.82" in page
    assert "composer-ingest review --accept" in page  # CLI hint shown for the queue
    assert "needs_review" in page  # status dropdown


def test_data_pages_show_error_banner_when_api_unreachable(
    monkeypatch: pytest.MonkeyPatch, staff_client: Client
) -> None:
    _install_data(monkeypatch, StubDataAPI(error="API unreachable at http://localhost:8000"))
    for url in ("/admin/data/", "/admin/data/entities/", "/admin/data/works/"):
        response = staff_client.get(url)
        assert response.status_code == 200
        assert "API unreachable" in response.content.decode()
