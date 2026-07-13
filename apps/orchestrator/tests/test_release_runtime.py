from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vanta_orchestrator.app import ALLOWED_HEADERS, ALLOWED_METHODS, AUTH_HEADER, create_app
from vanta_orchestrator.config import Settings
from vanta_orchestrator.main import self_test


def release_settings(tmp_path: Path) -> Settings:
    root = Path(__file__).resolve().parents[3]
    return Settings(
        data_dir=tmp_path / "Vanta",
        project_root=root,
        resource_root=root,
        logs_dir=tmp_path / "Vanta" / "logs",
        launch_token="test-token",
    )


def test_release_self_test_creates_database_migrations_and_logs(tmp_path: Path):
    settings = release_settings(tmp_path)
    assert self_test(settings) == 0
    assert settings.database_path.is_file()
    assert (settings.logs_dir / "orchestrator.log").is_file()


def test_protected_endpoints_reject_missing_launch_token(tmp_path: Path):
    settings = release_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").status_code == 401
        response = client.get("/api/health", headers={"X-Vanta-Token": "test-token"})
        assert response.status_code == 200
        assert response.json()["host"] == "127.0.0.1"


def test_packaged_webview_preflight_is_allowed_without_a_token(tmp_path: Path):
    settings = release_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.options(
            "/api/characters",
            headers={
                "Origin": "http://tauri.localhost",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "content-type,x-vanta-token",
            },
        )
    assert response.status_code in {200, 204}
    assert response.headers["access-control-allow-origin"] == "http://tauri.localhost"
    assert set(response.headers["access-control-allow-methods"].split(", ")) == set(ALLOWED_METHODS)
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    for header in ALLOWED_HEADERS:
        assert header.lower() in allowed_headers


def test_protected_requests_still_require_the_exact_launch_token(tmp_path: Path):
    settings = release_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/characters").status_code == 401
        assert client.get("/api/characters", headers={AUTH_HEADER: "invalid"}).status_code == 401
        assert client.get("/api/characters", headers={AUTH_HEADER: "test-token"}).status_code == 200


def test_unapproved_origins_are_rejected_and_dev_origins_are_opt_in(tmp_path: Path):
    settings = release_settings(tmp_path)
    request_headers = {
        "Origin": "https://example.invalid",
        "Access-Control-Request-Method": "GET",
    }
    with TestClient(create_app(settings)) as client:
        assert client.options("/api/characters", headers=request_headers).status_code == 400
        assert (
            client.options(
                "/api/characters",
                headers={**request_headers, "Origin": "http://127.0.0.1:1420"},
            ).status_code
            == 400
        )

    development = replace(settings, runtime_mode="development")
    with TestClient(create_app(development)) as client:
        assert client.options(
            "/api/characters",
            headers={**request_headers, "Origin": "http://127.0.0.1:1420"},
        ).status_code in {200, 204}


def test_diagnostics_export_is_sanitized(tmp_path: Path):
    settings = release_settings(tmp_path)
    settings.ensure_runtime_paths()
    (settings.logs_dir / "orchestrator.log").write_text(
        "token=test-token\nready\n", encoding="utf-8"
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/diagnostics/export", headers={"X-Vanta-Token": "test-token"})
    assert response.status_code == 200
    assert response.content.startswith(b"PK")
    assert b"test-token" not in response.content


def test_loopback_configuration_rejects_non_loopback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VANTA_HOST", "0.0.0.0")
    with pytest.raises(ValueError, match="loopback"):
        Settings.from_env()


def test_missing_bundled_resources_fail_self_test(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "Vanta",
        resource_root=tmp_path / "missing-resources",
        project_root=tmp_path / "missing-project",
        logs_dir=tmp_path / "Vanta" / "logs",
    )
    assert self_test(settings) == 1
