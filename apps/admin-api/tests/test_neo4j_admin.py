"""Admin API tests for the gold → Neo4j export endpoints.

No Neo4j instance is involved: the export itself is tested in
``packages/composer-neo4j``, so these cover the endpoint contract — how an
unconfigured, unreachable, busy or never-run target is reported.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import composer_admin.neo4j_export as neo4j_export
import pytest
from composer_admin import admin_app
from composer_neo4j import ExportConfig
from composer_warehouse.build import BuildManifest, write_build_manifest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    from composer_config import settings

    monkeypatch.setattr(settings, "admin_api_key", "test-key")
    # Each test gets its own manifest, and an unconfigured target by default, so
    # nothing here can reach a real instance.
    monkeypatch.setattr(neo4j_export, "read_export_manifest", lambda: _manifest(tmp_path))
    monkeypatch.setattr(neo4j_export, "is_configured", lambda: False)
    yield TestClient(admin_app, headers={"X-Admin-Key": "test-key"})


def _manifest(tmp_path: Path) -> BuildManifest | None:
    from composer_warehouse.build import read_build_manifest

    return read_build_manifest(tmp_path / "neo4j")


def _configure(monkeypatch: pytest.MonkeyPatch, *, reachable: bool = True) -> None:
    base = ExportConfig(uri="neo4j+s://abc123.databases.neo4j.io", user="abc123", password="secret")
    monkeypatch.setattr(neo4j_export, "is_configured", lambda: True)
    # honour the overrides the route passes, the way the real one does
    monkeypatch.setattr(neo4j_export, "config_from_settings", lambda **kw: base.with_overrides(**kw))
    if reachable:
        monkeypatch.setattr(neo4j_export, "verify", lambda _config: None)
    else:
        def explode(_config: ExportConfig) -> None:
            raise ConnectionError("nope")

        monkeypatch.setattr(neo4j_export, "verify", explode)


def test_status_reports_an_unconfigured_target(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(neo4j_export, "is_configured", lambda: False)
    data = client.get("/admin/v1/neo4j").json()

    assert data["configured"] is False
    assert data["reachable"] is None
    assert "NEO4J_URI" in data["detail"]
    assert data["status"] is None


def test_export_is_refused_when_unconfigured(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(neo4j_export, "is_configured", lambda: False)
    assert client.post("/admin/v1/neo4j/promote").status_code == 503


def test_status_reports_reachability(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    data = client.get("/admin/v1/neo4j").json()

    assert data["configured"] is True
    assert data["reachable"] is True
    assert data["detail"] is None


def test_status_reports_an_unreachable_target_without_failing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, reachable=False)
    response = client.get("/admin/v1/neo4j")

    assert response.status_code == 200  # a dead instance is a status, not an error
    assert response.json()["reachable"] is False
    assert "nope" in response.json()["detail"]


def test_status_never_leaks_the_credentials(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    body = client.get("/admin/v1/neo4j").text

    assert "abc123.databases.neo4j.io" in body
    assert "secret" not in body


def test_probe_can_be_skipped(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A poll during a running export should not open a connection."""
    _configure(monkeypatch)
    probed: list[bool] = []
    monkeypatch.setattr(neo4j_export, "verify", lambda _c: probed.append(True))

    client.get("/admin/v1/neo4j", params={"probe": False})
    assert probed == []


def test_export_conflicts_while_running(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch)
    write_build_manifest(tmp_path / "neo4j", BuildManifest.start())

    assert client.post("/admin/v1/neo4j/promote").status_code == 409


def test_export_starts_in_the_background(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    started: list[ExportConfig] = []
    monkeypatch.setattr(neo4j_export, "export_in_background", started.append)
    # the route holds its own reference to the function
    import composer_admin.build_routes as build_routes

    monkeypatch.setattr(build_routes, "export_in_background", started.append)

    response = client.post(
        "/admin/v1/neo4j/promote", json={"include_unperformed_works": True, "wipe_first": False}
    )

    assert response.status_code == 202
    assert response.json()["status"] == "running"
    assert len(started) == 1
    assert started[0].include_unperformed_works is True
    assert started[0].wipe_first is False


def test_export_defaults_to_performed_works_only(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The expensive scope must be opt-in: it takes Aura Free to ~95% of its cap."""
    _configure(monkeypatch)
    started: list[ExportConfig] = []
    import composer_admin.build_routes as build_routes

    monkeypatch.setattr(build_routes, "export_in_background", started.append)

    client.post("/admin/v1/neo4j/promote")

    assert started[0].include_unperformed_works is False


def test_status_carries_the_last_export_manifest(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch)
    manifest = BuildManifest.start().completed({"nodes": 68391, "relationships": 233816})
    write_build_manifest(tmp_path / "neo4j", manifest)

    data: dict[str, Any] = client.get("/admin/v1/neo4j").json()

    assert data["status"] == "completed"
    assert data["stats"]["nodes"] == 68391
