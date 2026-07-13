from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def bundled_resource_root() -> Path:
    """Return PyInstaller's extracted resource root, or the source repository root."""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[4]


def default_data_dir() -> Path:
    local_app_data = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local_app_data / "Vanta"


@dataclass(frozen=True, slots=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 47831
    data_dir: Path = Path("data/runtime")
    project_root: Path = Path(__file__).resolve().parents[4]
    resource_root: Path | None = None
    logs_dir: Path | None = None
    launch_token: str | None = None
    runtime_mode: str = "production"
    diagnostics_enabled: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        host = os.getenv("VANTA_HOST", "127.0.0.1")
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("Vanta only permits loopback service binding")
        port = int(os.getenv("VANTA_PORT", "47831"))
        if not 1 <= port <= 65535:
            raise ValueError("Vanta service port must be between 1 and 65535")
        data_dir = Path(os.getenv("VANTA_DATA_DIR", default_data_dir()))
        runtime_mode = os.getenv("VANTA_RUNTIME_MODE", "production").lower()
        if runtime_mode not in {"development", "production"}:
            raise ValueError("Vanta runtime mode must be development or production")
        return cls(
            host="127.0.0.1",
            port=port,
            data_dir=data_dir,
            resource_root=bundled_resource_root(),
            logs_dir=Path(os.getenv("VANTA_LOGS_DIR", data_dir / "logs")),
            launch_token=os.getenv("VANTA_LAUNCH_TOKEN") or None,
            runtime_mode=runtime_mode,
            diagnostics_enabled=os.getenv("VANTA_DIAGNOSTICS") == "1",
        )

    @property
    def allowed_origins(self) -> list[str]:
        """Origins used by Vanta's local WebView, with dev origins opt-in only."""
        origins = ["http://tauri.localhost"]
        if self.runtime_mode == "development":
            origins.extend(["http://127.0.0.1:1420", "http://localhost:1420"])
        return origins

    @property
    def database_path(self) -> Path:
        return self.data_dir / "vanta.db"

    @property
    def engine_root(self) -> Path:
        return self.data_dir / "engine"

    @property
    def runtime_root(self) -> Path:
        return self.engine_root / "comfyui"

    @property
    def model_root(self) -> Path:
        return self.engine_root / "models" / "checkpoints"

    @property
    def media_root(self) -> Path:
        return self.data_dir / "media" / "generations"

    @property
    def migrations_dir(self) -> Path:
        root = self.resource_root or self.project_root
        candidate = root / "migrations"
        return (
            candidate
            if candidate.exists()
            else self.project_root / "apps" / "orchestrator" / "migrations"
        )

    @property
    def starter_presets_path(self) -> Path:
        root = self.resource_root or self.project_root
        candidate = root / "data" / "starter_presets.json"
        return (
            candidate if candidate.exists() else self.project_root / "data" / "starter_presets.json"
        )

    @property
    def engine_manifest_dir(self) -> Path:
        root = self.resource_root or self.project_root
        candidate = root / "engine" / "manifests"
        return candidate if candidate.exists() else self.project_root / "engine" / "manifests"

    def ensure_runtime_paths(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir or self.data_dir / "logs").mkdir(parents=True, exist_ok=True)
        self.engine_root.mkdir(parents=True, exist_ok=True)
        self.model_root.mkdir(parents=True, exist_ok=True)
        self.media_root.mkdir(parents=True, exist_ok=True)
