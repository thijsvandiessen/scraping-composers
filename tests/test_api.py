"""API endpoint tests using an in-memory database."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import composer_ingest.api as api_module
from composer_ingest.api import app
from composer_ingest.db import init_db
from composer_ingest.document import Document, SourceClaim, entity_document, stamp
from composer_ingest.ingest import run_ingest


def _person(name: str, *claims: SourceClaim, external_id: str | None = None) -> Document:
    return entity_document(
        id=external_id or f"id:{name}",
        name=name,
        url=None,
        raw={"id": name},
        claims=claims,
    )


class _FakeSource:
    NAME = "fake"
    BASE_URL = "https://fake.example"

    def __init__(self, records: list[Document]) -> None:
        self._records = records

    def fetch_documents(self, max_pages: int | None = None) -> Iterator[Document]:
        for record in self._records:
            yield stamp(record, self.NAME)


@pytest.fixture
def client() -> Iterator[TestClient]:
    # StaticPool shares one in-memory connection across all sessions so the
    # seeded data is visible to every request the TestClient makes.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = init_db(engine)
    source = _FakeSource(
        [
            _person(
                "Doe, Jane",
                SourceClaim("has_profession", "profession", "soloist"),
                SourceClaim("performs_as", value="violin"),
            ),
            _person("Smith, John", SourceClaim("has_profession", "profession", "conductor")),
            _person("Bach, Johann", SourceClaim("has_profession", "profession", "composer")),
            _person(
                "Multi, Person",
                SourceClaim("has_profession", "profession", "soloist"),
                SourceClaim("has_profession", "profession", "conductor"),
            ),
        ]
    )
    with factory() as s:
        run_ingest(s, source)

    original = api_module._session_factory
    api_module._session_factory = factory
    yield TestClient(app)
    api_module._session_factory = original


# --- /v1/soloists ---


def test_list_soloists_returns_only_soloists(client: TestClient) -> None:
    r = client.get("/v1/soloists")
    assert r.status_code == 200
    data = r.json()
    labels = {item["label"] for item in data["items"]}
    assert "Doe, Jane" in labels
    assert "Multi, Person" in labels
    assert "Smith, John" not in labels
    assert "Bach, Johann" not in labels


def test_list_soloists_pagination(client: TestClient) -> None:
    r = client.get("/v1/soloists?limit=1&page=1")
    assert r.status_code == 200
    data = r.json()
    assert data["limit"] == 1
    assert data["total"] == 2
    assert len(data["items"]) == 1


def test_list_soloists_search(client: TestClient) -> None:
    r = client.get("/v1/soloists?q=Doe")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["label"] == "Doe, Jane"


def test_get_soloist_returns_detail(client: TestClient) -> None:
    listing = client.get("/v1/soloists").json()
    jane = next(i for i in listing["items"] if i["label"] == "Doe, Jane")
    r = client.get(f"/v1/soloists/{jane['id']}")
    assert r.status_code == 200
    data = r.json()
    assert data["label"] == "Doe, Jane"
    predicates = {c["predicate"] for c in data["claims"]}
    assert "has_profession" in predicates
    assert "performs_as" in predicates


def test_get_soloist_404_for_conductor(client: TestClient) -> None:
    listing = client.get("/v1/conductors").json()
    smith = next(i for i in listing["items"] if i["label"] == "Smith, John")
    r = client.get(f"/v1/soloists/{smith['id']}")
    assert r.status_code == 404


def test_get_soloist_404_for_missing(client: TestClient) -> None:
    r = client.get("/v1/soloists/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


# --- /v1/conductors ---


def test_list_conductors_returns_only_conductors(client: TestClient) -> None:
    r = client.get("/v1/conductors")
    assert r.status_code == 200
    data = r.json()
    labels = {item["label"] for item in data["items"]}
    assert "Smith, John" in labels
    assert "Multi, Person" in labels
    assert "Doe, Jane" not in labels
    assert "Bach, Johann" not in labels


def test_list_conductors_search(client: TestClient) -> None:
    r = client.get("/v1/conductors?q=Smith")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["label"] == "Smith, John"


def test_get_conductor_returns_detail(client: TestClient) -> None:
    listing = client.get("/v1/conductors").json()
    smith = next(i for i in listing["items"] if i["label"] == "Smith, John")
    r = client.get(f"/v1/conductors/{smith['id']}")
    assert r.status_code == 200
    data = r.json()
    assert data["label"] == "Smith, John"
    assert any(c["predicate"] == "has_profession" for c in data["claims"])


def test_get_conductor_404_for_soloist(client: TestClient) -> None:
    listing = client.get("/v1/soloists").json()
    jane = next(i for i in listing["items"] if i["label"] == "Doe, Jane")
    r = client.get(f"/v1/conductors/{jane['id']}")
    assert r.status_code == 404


def test_get_conductor_404_for_missing(client: TestClient) -> None:
    r = client.get("/v1/conductors/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_person_with_both_roles_appears_in_both_lists(client: TestClient) -> None:
    soloists = {i["label"] for i in client.get("/v1/soloists").json()["items"]}
    conductors = {i["label"] for i in client.get("/v1/conductors").json()["items"]}
    assert "Multi, Person" in soloists
    assert "Multi, Person" in conductors


# --- /v1/composers ---


def test_list_composers_returns_all_persons(client: TestClient) -> None:
    r = client.get("/v1/composers")
    assert r.status_code == 200
    data = r.json()
    # all 4 ingested persons appear — composers endpoint has no profession filter
    assert data["total"] == 4
    labels = {item["label"] for item in data["items"]}
    assert "Bach, Johann" in labels
    assert "Doe, Jane" in labels
    assert "Smith, John" in labels


def test_list_composers_profession_entities_excluded(client: TestClient) -> None:
    r = client.get("/v1/composers")
    assert r.status_code == 200
    labels = {item["label"] for item in r.json()["items"]}
    # kind=profession entities (e.g. "composer", "soloist") must not appear
    assert "composer" not in labels
    assert "soloist" not in labels


def test_list_composers_search_filter(client: TestClient) -> None:
    r = client.get("/v1/composers?q=Bach")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["label"] == "Bach, Johann"


def test_list_composers_pagination(client: TestClient) -> None:
    r = client.get("/v1/composers?page=1&limit=2")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 4
    assert data["limit"] == 2
    assert len(data["items"]) == 2


def test_list_composers_invalid_page_returns_422(client: TestClient) -> None:
    assert client.get("/v1/composers?page=0").status_code == 422


def test_list_composers_invalid_limit_returns_422(client: TestClient) -> None:
    assert client.get("/v1/composers?limit=0").status_code == 422
    assert client.get("/v1/composers?limit=101").status_code == 422


def test_get_composer_returns_detail_with_claims(client: TestClient) -> None:
    listing = client.get("/v1/composers").json()
    bach = next(i for i in listing["items"] if i["label"] == "Bach, Johann")
    r = client.get(f"/v1/composers/{bach['id']}")
    assert r.status_code == 200
    data = r.json()
    assert data["label"] == "Bach, Johann"
    assert data["kind"] == "person"
    predicates = {c["predicate"] for c in data["claims"]}
    assert "has_profession" in predicates


def test_get_composer_not_found(client: TestClient) -> None:
    r = client.get("/v1/composers/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_get_composer_invalid_uuid_path_returns_422(client: TestClient) -> None:
    assert client.get("/v1/composers/not-a-uuid").status_code == 422


def test_list_soloists_invalid_page_returns_422(client: TestClient) -> None:
    assert client.get("/v1/soloists?page=0").status_code == 422


def test_list_soloists_invalid_limit_returns_422(client: TestClient) -> None:
    assert client.get("/v1/soloists?limit=0").status_code == 422
    assert client.get("/v1/soloists?limit=101").status_code == 422


def test_list_conductors_invalid_page_returns_422(client: TestClient) -> None:
    assert client.get("/v1/conductors?page=0").status_code == 422


def test_list_conductors_invalid_limit_returns_422(client: TestClient) -> None:
    assert client.get("/v1/conductors?limit=0").status_code == 422
    assert client.get("/v1/conductors?limit=101").status_code == 422
