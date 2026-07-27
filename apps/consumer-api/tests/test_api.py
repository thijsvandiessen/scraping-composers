# pylint: disable=too-many-lines
"""Consumer API tests: the silver app over staging seed data, the gold app over
its promoted copy — both using in-memory/tmp databases, no network."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from composer_api import create_app
from composer_gold import promote
from composer_schema import EntityDocument, SourceAdapter, SourceClaim
from composer_warehouse.concerts import derive_concerts
from composer_warehouse.db import init_db
from composer_warehouse.recordings import derive_recordings
from composer_warehouse.testing import FakeSource, ingest_source, mention, perf_mention
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

_INGESTED_AT = datetime(2024, 1, 1, tzinfo=UTC)


def _person(
    name: str, *claims: SourceClaim, external_id: str | None = None, url: str | None = None
) -> EntityDocument:
    return EntityDocument(
        id=external_id or f"id:{name}",
        url=url,
        source_name="fake",
        ingested_at=_INGESTED_AT,
        name=name,
        raw={"id": name},
        claims=claims,
    )


class _FakeSource(SourceAdapter):
    name = "fake"
    base_url = "https://fake.example"

    def __init__(self, records: list[EntityDocument]) -> None:
        self._records = records

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument]:
        yield from self._records


def _seeded_factory() -> sessionmaker[Session]:
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
            # Wikidata-style record: the source's own id plus the exact page URL
            _person(
                "Beethoven, Ludwig van",
                SourceClaim("has_profession", "profession", "composer"),
                external_id="Q255",
                url="https://fake.example/wiki/Q255",
            ),
            _person(
                "Multi, Person",
                SourceClaim("has_profession", "profession", "soloist"),
                SourceClaim("has_profession", "profession", "conductor"),
            ),
        ]
    )
    programmes = FakeSource(
        records=(
            mention("Symphony No. 5, Op. 67", "Beethoven, Ludwig van", "m1"),
            mention("Sinfonie Nr. 5, op. 67", "Beethoven, Ludwig van", "m2"),
        ),
        name="programmes",
        base_url="https://programmes.example",
    )
    with factory() as s:
        ingest_source(s, source)
        ingest_source(s, programmes)
    return factory


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Client over the silver app: staging data, nothing curated away."""
    factory = _seeded_factory()
    yield TestClient(create_app("test-silver", lambda: factory))


@pytest.fixture
def gold_client(tmp_path: Path) -> Iterator[TestClient]:
    """Client over the gold app: the same seed, promoted."""
    factory = _seeded_factory()
    gold_path = tmp_path / "gold.db"
    with factory() as s:
        promote(s, gold_path)
    gold_factory = init_db(create_engine(f"sqlite:///{gold_path}"))
    yield TestClient(create_app("test-gold", lambda: gold_factory))


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
    assert data["total"] == 2
    labels = {item["label"] for item in data["items"]}
    assert "Bach, Johann" in labels
    assert "Beethoven, Ludwig van" in labels
    assert "Doe, Jane" not in labels
    assert "Smith, John" not in labels


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
    r = client.get("/v1/composers?page=1&limit=1")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    assert data["limit"] == 1
    assert len(data["items"]) == 1


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


def test_claim_source_url_is_record_page(client: TestClient) -> None:
    """Claims point at the exact source page they came from, not the source homepage."""
    listing = client.get("/v1/composers").json()
    beethoven = next(i for i in listing["items"] if i["label"] == "Beethoven, Ludwig van")
    data = client.get(f"/v1/composers/{beethoven['id']}").json()
    profession = next(c for c in data["claims"] if c["predicate"] == "has_profession")
    assert profession["source"] == "fake"
    assert profession["source_url"] == "https://fake.example/wiki/Q255"
    assert profession["source_external_id"] == "Q255"


def test_claim_source_url_falls_back_to_base_url(client: TestClient) -> None:
    """Records without a page URL fall back to the source homepage."""
    listing = client.get("/v1/composers").json()
    bach = next(i for i in listing["items"] if i["label"] == "Bach, Johann")
    data = client.get(f"/v1/composers/{bach['id']}").json()
    profession = next(c for c in data["claims"] if c["predicate"] == "has_profession")
    assert profession["source_url"] == "https://fake.example"


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


# --- /v1/stats ---


def test_stats_reports_dataset_counts(client: TestClient) -> None:
    r = client.get("/v1/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["entities_by_kind"]["person"] == 5
    assert data["entities_by_kind"]["profession"] == 3  # soloist, conductor, composer
    assert data["entities_total"] == sum(data["entities_by_kind"].values())
    assert data["records_by_source"] == {"fake": 5}  # mentions are not entity records
    assert data["works"] == 1  # both mentions resolve to one work (matching Op. 67)
    assert data["work_mentions"] == 2
    assert sum(data["mentions_by_status"].values()) == 2


# --- /v1/entities ---


def test_list_entities_searches_all_kinds(client: TestClient) -> None:
    r = client.get("/v1/entities?q=soloist")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["kind"] == "profession"


def test_list_entities_kind_filter(client: TestClient) -> None:
    r = client.get("/v1/entities?kind=profession")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 3
    assert {item["label"] for item in data["items"]} == {"soloist", "conductor", "composer"}


def test_entity_detail_links_claim_objects(client: TestClient) -> None:
    jane = client.get("/v1/entities?q=Doe").json()["items"][0]
    r = client.get(f"/v1/entities/{jane['id']}")
    assert r.status_code == 200
    data = r.json()
    assert data["label"] == "Doe, Jane"
    profession = next(c for c in data["claims"] if c["predicate"] == "has_profession")
    assert profession["object_label"] == "soloist"
    assert profession["object_id"] is not None  # navigable to the profession entity
    literal = next(c for c in data["claims"] if c["predicate"] == "performs_as")
    assert (literal["value"], literal["object_id"]) == ("violin", None)


def test_entity_detail_reports_incoming_claims(client: TestClient) -> None:
    soloist = client.get("/v1/entities?q=soloist&kind=profession").json()["items"][0]
    r = client.get(f"/v1/entities/{soloist['id']}")
    assert r.status_code == 200
    data = r.json()
    assert data["incoming_total"] == 2  # Doe, Jane + Multi, Person
    subjects = {c["subject_label"] for c in data["incoming"]}
    assert subjects == {"Doe, Jane", "Multi, Person"}
    assert all(c["predicate"] == "has_profession" for c in data["incoming"])


def test_entity_detail_404_for_missing(client: TestClient) -> None:
    assert client.get("/v1/entities/00000000-0000-0000-0000-000000000000").status_code == 404


def test_crud_not_found_surfaces_as_404_with_detail(client: TestClient, gold_client: TestClient) -> None:
    """A NotFoundError raised in crud maps to a 404 whose body matches
    FastAPI's HTTPException shape, on both the silver and gold apps."""
    for app_client in (client, gold_client):
        r = app_client.get("/v1/entities/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404
        assert r.json() == {"detail": "entity not found"}


def test_list_entities_random_order_samples(client: TestClient) -> None:
    r = client.get("/v1/entities?order=random&kind=person&limit=3")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 5  # total still reports the full population
    assert len(data["items"]) == 3
    assert all(item["kind"] == "person" for item in data["items"])


def test_list_entities_rejects_unknown_order(client: TestClient) -> None:
    assert client.get("/v1/entities?order=nope").status_code == 422


# --- /v1/mentions ---


@pytest.fixture
def review_client() -> Iterator[TestClient]:
    """Client over a dataset where one mention landed in needs_review."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = init_db(engine)
    programmes = FakeSource(
        records=(
            # similar-but-not-identical titles: the second scores in the review band
            mention("Songs of a Wayfarer", "Mahler, Gustav", "m1"),
            mention("Songs of a Traveller", "Mahler, Gustav", "m2"),
        ),
        name="programmes",
        base_url="https://programmes.example",
    )
    with factory() as s:
        ingest_source(s, programmes)

    yield TestClient(create_app("test-silver", lambda: factory))


def test_mentions_needs_review_lists_queue_with_candidate(review_client: TestClient) -> None:
    r = review_client.get("/v1/mentions?status=needs_review")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    queued = data["items"][0]
    assert queued["title"] == "Songs of a Traveller"
    assert queued["composer"] == "Mahler, Gustav"
    assert queued["status"] == "needs_review"
    assert queued["score"] is not None
    assert queued["candidate_title"] == "Songs of a Wayfarer"
    assert queued["candidate_work_id"] is not None
    assert queued["work_id"] is None  # not resolved yet


def test_mentions_unfiltered_includes_resolved(review_client: TestClient) -> None:
    r = review_client.get("/v1/mentions")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    by_status = {m["status"]: m for m in data["items"]}
    assert by_status["created"]["work_title"] == "Songs of a Wayfarer"  # resolved to its work


# --- /v1/works ---


def test_list_works_with_aliases_and_mention_count(client: TestClient) -> None:
    r = client.get("/v1/works")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    work = data["items"][0]
    assert work["canonical_title"] == "Symphony No. 5, Op. 67"
    assert work["composer_label"] == "Beethoven, Ludwig van"
    assert work["composer_id"] is not None
    assert work["mention_count"] == 2
    assert "Sinfonie Nr. 5, op. 67" in work["aliases"]
    assert work["opus_number"] == "67"


def test_list_works_searches_by_composer(client: TestClient) -> None:
    assert client.get("/v1/works?q=Beethoven").json()["total"] == 1
    assert client.get("/v1/works?q=Nobody").json()["total"] == 0


# --- concerts (gold app) ---


@pytest.fixture
def concerts_client(tmp_path: Path) -> Iterator[TestClient]:
    """Gold client over a dataset with derived concerts."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = init_db(engine)
    berlinphil = FakeSource(
        records=(
            perf_mention(
                "perf:1-1",
                "Ein Heldenleben",
                "Richard Strauss",
                {
                    "concert_id": "1",
                    "date": "1985-03-01",
                    "season": "1984/85",
                    "url": "https://dch.example/1",
                    "conductors": ["Karajan, Herbert von"],
                    "soloists": [{"name": "Mutter, Anne-Sophie", "discipline": "violin"}],
                },
            ),
            perf_mention(
                "perf:2-1",
                "Symphonie fantastique",
                "Hector Berlioz",
                {
                    "concert_id": "2",
                    "date": "1987-05-12",
                    "url": "https://dch.example/2",
                    "conductors": ["Karajan, Herbert von"],
                },
            ),
            perf_mention(
                "perf:3-1",
                "Symphony No. 9",
                "Gustav Mahler",
                {
                    "concert_id": "3",
                    "date": "1999-10-02",
                    "url": "https://dch.example/3",
                    "conductors": ["Abbado, Claudio"],
                },
            ),
            _person("Karajan, Herbert von", SourceClaim("has_profession", "profession", "conductor")),
            _person("Abbado, Claudio", SourceClaim("has_profession", "profession", "conductor")),
            _person("Mutter, Anne-Sophie", SourceClaim("has_profession", "profession", "soloist")),
        ),
        name="berlinphil",
        base_url="https://bp.example",
    )
    with factory() as s:
        ingest_source(s, berlinphil)
        derive_concerts(s)
        gold_path = tmp_path / "gold.db"
        promote(s, gold_path)
    gold_factory = init_db(create_engine(f"sqlite:///{gold_path}"))
    yield TestClient(create_app("test-gold-concerts", lambda: gold_factory))


def test_conductors_sortable_by_concert_count(concerts_client: TestClient) -> None:
    data = concerts_client.get("/v1/conductors?sort=concerts").json()
    assert [(i["label"], i["concert_count"]) for i in data["items"]] == [
        ("Karajan, Herbert von", 2),
        ("Abbado, Claudio", 1),
    ]
    # default sort stays alphabetical, counts still present
    by_label = concerts_client.get("/v1/conductors").json()
    assert [i["label"] for i in by_label["items"]] == ["Abbado, Claudio", "Karajan, Herbert von"]


def test_person_concerts_lists_newest_first_with_works(concerts_client: TestClient) -> None:
    karajan = concerts_client.get("/v1/conductors?q=Karajan").json()["items"][0]
    data = concerts_client.get(f"/v1/people/{karajan['id']}/concerts").json()
    assert data["person_label"] == "Karajan, Herbert von"
    assert data["total"] == 2
    assert [c["date"] for c in data["items"]] == ["1987-05-12", "1985-03-01"]  # newest first
    assert data["items"][0]["works"] == ["Symphonie fantastique"]
    assert data["items"][0]["role"] == "conductor"
    assert data["items"][0]["url"] == "https://dch.example/2"
    assert data["items"][0]["source"] == "berlinphil"


def test_concerts_list_newest_first_with_summaries(concerts_client: TestClient) -> None:
    data = concerts_client.get("/v1/concerts").json()
    assert data["total"] == 3
    assert [c["date"] for c in data["items"]] == ["1999-10-02", "1987-05-12", "1985-03-01"]
    heldenleben = data["items"][2]
    assert heldenleben["conductors"] == ["Karajan, Herbert von"]
    assert heldenleben["soloist_count"] == 1
    assert heldenleben["work_count"] == 1
    assert heldenleben["season"] == "1984/85"
    assert heldenleben["source"] == "berlinphil"


def test_concerts_list_search_by_participant_and_source(concerts_client: TestClient) -> None:
    assert concerts_client.get("/v1/concerts?q=Karajan").json()["total"] == 2
    assert concerts_client.get("/v1/concerts?q=Mutter").json()["total"] == 1  # soloist name matches too
    assert concerts_client.get("/v1/concerts?source=berlinphil").json()["total"] == 3
    assert concerts_client.get("/v1/concerts?source=nyphil").json()["total"] == 0


def test_concert_detail_has_participants_and_programme(concerts_client: TestClient) -> None:
    concert_id = concerts_client.get("/v1/concerts?q=Mutter").json()["items"][0]["id"]
    data = concerts_client.get(f"/v1/concerts/{concert_id}").json()
    assert data["date"] == "1985-03-01"
    assert data["url"] == "https://dch.example/1"
    by_role = {p["role"]: p for p in data["participants"]}
    assert by_role["conductor"]["name"] == "Karajan, Herbert von"
    assert by_role["conductor"]["entity_id"] is not None
    assert by_role["soloist"]["name"] == "Mutter, Anne-Sophie"
    assert by_role["soloist"]["discipline"] == "violin"
    assert by_role["soloist"]["entity_id"] is not None
    assert data["works"] == [{"title": "Ein Heldenleben", "composer": "Richard Strauss"}]


def test_concert_detail_404(concerts_client: TestClient) -> None:
    assert concerts_client.get("/v1/concerts/999").status_code == 404


def test_person_concerts_404_and_invalid_sort(concerts_client: TestClient) -> None:
    assert concerts_client.get("/v1/people/00000000-0000-0000-0000-000000000000/concerts").status_code == 404
    assert concerts_client.get("/v1/conductors?sort=nope").status_code == 422


def _recording_raw(catalogue: str, works_title: str) -> dict[str, object]:
    return {
        "_source": "llm",
        "_kind": "recording",
        "record_key": f"https://dg.example/{catalogue}",
        "url": f"https://dg.example/{catalogue}",
        "title": f"Album {works_title}",
        "release_date": "2024-03-15" if catalogue == "a1" else "2020-01-01",
        "label": "Deutsche Grammophon",
        "catalogue_number": catalogue,
        "format": "CD",
        "artists": [
            {"name": "Rattle, Simon", "role": "conductor", "discipline": None},
            {"name": "Jansen, Janine", "role": "soloist", "discipline": "violin"},
        ],
    }


@pytest.fixture
def recordings_client(tmp_path: Path) -> Iterator[TestClient]:
    """Gold client over a dataset with derived recordings."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = init_db(engine)
    dg = FakeSource(
        records=(
            perf_mention("r:a1#w0", "Symphony No. 9", "Beethoven", _recording_raw("a1", "Beethoven")),
            perf_mention("r:a2#w0", "The Four Seasons", "Vivaldi", _recording_raw("a2", "Vivaldi")),
            _person("Rattle, Simon", SourceClaim("has_profession", "profession", "conductor")),
            _person("Jansen, Janine", SourceClaim("has_profession", "profession", "soloist")),
        ),
        name="deutschegrammophon",
        base_url="https://dg.example",
    )
    with factory() as s:
        ingest_source(s, dg)
        derive_recordings(s)
        gold_path = tmp_path / "gold.db"
        promote(s, gold_path)
    gold_factory = init_db(create_engine(f"sqlite:///{gold_path}"))
    yield TestClient(create_app("test-gold-recordings", lambda: gold_factory))


def test_recordings_list_newest_first_with_summaries(recordings_client: TestClient) -> None:
    data = recordings_client.get("/v1/recordings").json()
    assert data["total"] == 2
    assert [r["release_date"] for r in data["items"]] == ["2024-03-15", "2020-01-01"]  # newest first
    top = data["items"][0]
    assert top["title"] == "Album Beethoven"
    assert top["label"] == "Deutsche Grammophon"
    assert top["catalogue_number"] == "a1"
    assert top["conductors"] == ["Rattle, Simon"]
    assert top["performer_count"] == 1  # the violinist
    assert top["work_count"] == 1
    assert top["source"] == "deutschegrammophon"


def test_recordings_list_search_by_participant_and_source(recordings_client: TestClient) -> None:
    assert recordings_client.get("/v1/recordings?q=Beethoven").json()["total"] == 1  # title match
    assert recordings_client.get("/v1/recordings?q=Jansen").json()["total"] == 2  # artist on both
    assert recordings_client.get("/v1/recordings?source=deutschegrammophon").json()["total"] == 2
    assert recordings_client.get("/v1/recordings?source=nyphil").json()["total"] == 0


def test_recording_detail_has_artists_and_works(recordings_client: TestClient) -> None:
    recording_id = recordings_client.get("/v1/recordings?q=Beethoven").json()["items"][0]["id"]
    data = recordings_client.get(f"/v1/recordings/{recording_id}").json()
    assert data["catalogue_number"] == "a1"
    assert data["format"] == "CD"
    by_role = {p["role"]: p for p in data["participants"]}
    assert by_role["conductor"]["name"] == "Rattle, Simon"
    assert by_role["conductor"]["entity_id"] is not None
    assert by_role["soloist"]["discipline"] == "violin"
    assert data["works"] == [{"title": "Symphony No. 9", "composer": "Beethoven"}]


def test_recording_detail_404(recordings_client: TestClient) -> None:
    assert recordings_client.get("/v1/recordings/999").status_code == 404


def test_person_recordings_lists_credits(recordings_client: TestClient) -> None:
    rattle = recordings_client.get("/v1/conductors?q=Rattle").json()["items"][0]
    data = recordings_client.get(f"/v1/people/{rattle['id']}/recordings").json()
    assert data["person_label"] == "Rattle, Simon"
    assert data["total"] == 2
    assert [r["release_date"] for r in data["items"]] == ["2024-03-15", "2020-01-01"]  # newest first
    assert data["items"][0]["role"] == "conductor"
    assert data["items"][0]["works"] == ["Symphony No. 9"]


def test_person_recordings_404_for_missing(recordings_client: TestClient) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    assert recordings_client.get(f"/v1/people/{missing}/recordings").status_code == 404


def test_person_concerts_on_silver(tmp_path: Path) -> None:
    """Concerts are silver-derived, so the silver app serves them too."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = init_db(engine)
    berlinphil = FakeSource(
        records=(
            perf_mention(
                "perf:1-1",
                "Ein Heldenleben",
                "Richard Strauss",
                {"concert_id": "1", "date": "1985-03-01", "conductors": ["Karajan, Herbert von"]},
            ),
            _person("Karajan, Herbert von", SourceClaim("has_profession", "profession", "conductor")),
        ),
        name="berlinphil",
        base_url="https://bp.example",
    )
    with factory() as s:
        ingest_source(s, berlinphil)
        derive_concerts(s)
    silver = TestClient(create_app("test-silver-concerts", lambda: factory))

    assert silver.get("/v1/concerts").json()["total"] == 1
    karajan = silver.get("/v1/conductors?q=Karajan").json()["items"][0]
    data = silver.get(f"/v1/people/{karajan['id']}/concerts").json()
    assert data["total"] == 1
    assert data["items"][0]["works"] == ["Ein Heldenleben"]


# --- gold app: same routes over the curated database ---


def test_gold_hides_people_without_performance_evidence(gold_client: TestClient) -> None:
    # the four fake-source people have no mentions and no archive records;
    # only the mentions' composer survives promotion
    data = gold_client.get("/v1/composers").json()
    assert data["total"] == 1
    assert data["items"][0]["label"] == "Beethoven, Ludwig van"


def test_gold_claim_source_url_survives_promotion(gold_client: TestClient) -> None:
    """Promotion copies entity records, so gold claims still link the exact source page."""
    listing = gold_client.get("/v1/composers").json()
    beethoven = next(i for i in listing["items"] if i["label"] == "Beethoven, Ludwig van")
    data = gold_client.get(f"/v1/composers/{beethoven['id']}").json()
    profession = next(c for c in data["claims"] if c["predicate"] == "has_profession")
    assert profession["source_url"] == "https://fake.example/wiki/Q255"
    assert profession["source_external_id"] == "Q255"


def test_gold_keeps_works_with_mention_counts(gold_client: TestClient) -> None:
    data = gold_client.get("/v1/works").json()
    assert data["total"] == 1
    assert data["items"][0]["mention_count"] == 2
    assert data["items"][0]["composer_label"] == "Beethoven, Ludwig van"


def test_gold_stats_reflect_curation(gold_client: TestClient) -> None:
    stats = gold_client.get("/v1/stats").json()
    assert stats["entities_by_kind"]["person"] == 1
    assert stats["entities_by_kind"].get("profession") == 1  # only the referenced "composer" survives
