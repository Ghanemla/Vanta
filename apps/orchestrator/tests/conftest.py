from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vanta_orchestrator.app import create_app
from vanta_orchestrator.config import Settings


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    settings = Settings(data_dir=tmp_path, project_root=Path(__file__).resolve().parents[3])
    with TestClient(create_app(settings)) as test_client:
        yield test_client
