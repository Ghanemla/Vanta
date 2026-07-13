from __future__ import annotations

import hashlib
import json
import logging
import shutil
import socket
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import py7zr
from websockets.sync.client import connect

from .config import Settings

logger = logging.getLogger("vanta.orchestrator.comfy")

RUNTIME_ARCHIVE_NAME = "ComfyUI_windows_portable_nvidia-v0.27.0.7z"


def allocate_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_safe_archive_members(names: list[str]) -> None:
    for name in names:
        member = PurePosixPath(name.replace("\\", "/"))
        if member.is_absolute() or ".." in member.parts:
            raise RuntimeError("The managed engine archive contains an unsafe path")


def safe_extract_7z(archive: Path, destination: Path, extractor: Path) -> None:
    with py7zr.SevenZipFile(archive, mode="r") as bundle:
        ensure_safe_archive_members(bundle.getnames())
    if not extractor.is_file():
        raise RuntimeError("Vanta's reviewed archive extractor is missing; repair the application")
    completed = subprocess.run(
        [str(extractor), "x", "-y", f"-o{destination}", str(archive)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
        timeout=900,
    )
    if completed.returncode != 0:
        details = completed.stdout.decode("utf-8", errors="replace")[-800:].strip()
        raise RuntimeError(f"The reviewed local engine archive could not be extracted: {details}")


def validate_safetensors(path: Path) -> dict[str, Any]:
    if path.suffix.lower() != ".safetensors":
        raise ValueError("Choose a .safetensors checkpoint")
    if path.stat().st_size < 16:
        raise ValueError("This checkpoint is too small to be a safetensors model")
    with path.open("rb") as source:
        header_length = int.from_bytes(source.read(8), "little")
        if not 2 <= header_length <= 100 * 1024 * 1024:
            raise ValueError("This checkpoint has an invalid safetensors header")
        try:
            header = json.loads(source.read(header_length))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("This checkpoint has an unreadable safetensors header") from error
    if not isinstance(header, dict):
        raise ValueError("This checkpoint has an invalid safetensors structure")
    return header


@dataclass(slots=True)
class RuntimeSnapshot:
    state: str
    port: int | None
    message: str
    revision: str


class ManagedComfyRuntime:
    """Vanta-owned ComfyUI runtime. It never touches a user-installed engine."""

    def __init__(self, settings: Settings, source: dict[str, Any], revision: str):
        self.settings = settings
        self.source = source
        self.revision = revision
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._port: int | None = None
        self._state = "not_installed"
        self._message = "Local Generation Engine has not been installed"
        self._restart_count = 0
        self._cancel_install = threading.Event()

    @property
    def archive_path(self) -> Path:
        return self.settings.engine_root / "downloads" / RUNTIME_ARCHIVE_NAME

    @property
    def root(self) -> Path:
        return self.settings.runtime_root

    @property
    def extractor_path(self) -> Path:
        relative = str(self.source.get("extractor", {}).get("relative_path", ""))
        return self.settings.engine_manifest_dir.parent / relative

    def _verify_extractor(self) -> Path:
        expected = str(self.source.get("extractor", {}).get("sha256", ""))
        extractor = self.extractor_path
        if not expected or not extractor.is_file() or sha256_file(extractor) != expected:
            raise RuntimeError(
                "Vanta's reviewed archive extractor failed verification; repair the application"
            )
        return extractor

    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            self._refresh_process_state()
            return RuntimeSnapshot(self._state, self._port, self._message, self.revision)

    def installed_layout(self) -> tuple[Path, Path] | None:
        if not self.root.is_dir():
            return None
        main = next(self.root.rglob("main.py"), None)
        python = next(self.root.rglob("python_embeded/python.exe"), None)
        if main is None or python is None:
            return None
        return main, python

    def _set_state(self, state: str, message: str) -> None:
        with self._lock:
            self._state, self._message = state, message

    def _refresh_process_state(self) -> None:
        if self._process is None:
            return
        exit_code = self._process.poll()
        if exit_code is None:
            return
        self._process = None
        self._port = None
        if self._state not in {"stopped", "not_installed", "repair_needed"}:
            if self._restart_count < 1 and self.installed_layout() is not None:
                self._restart_count += 1
                self._state = "restarting"
                self._message = "Local Generation Engine stopped unexpectedly; restarting once"
                self.start()
            else:
                self._state = "crashed"
                self._message = f"Local Generation Engine stopped unexpectedly (exit {exit_code})"

    def _download(self) -> Path:
        url = str(self.source["url"])
        expected_hash = str(self.source["sha256"])
        expected_size = int(self.source["bytes"])
        self.archive_path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.archive_path.stat().st_size if self.archive_path.exists() else 0
        if existing == expected_size and sha256_file(self.archive_path) == expected_hash:
            return self.archive_path
        request = Request(url, headers={"User-Agent": "Vanta/0.1", "Range": f"bytes={existing}-"})
        try:
            with urlopen(request, timeout=30) as response:
                # Servers may ignore a Range request. Never append a complete
                # response to a partial archive, as that corrupts the runtime.
                resumed = existing > 0 and response.status == 206
                mode = "ab" if resumed else "wb"
                with self.archive_path.open(mode) as target:
                    while True:
                        if self._cancel_install.is_set():
                            raise RuntimeError("Local engine installation was cancelled")
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        target.write(chunk)
        except URLError as error:
            raise RuntimeError(
                "Vanta could not download the reviewed local engine runtime"
            ) from error
        if self.archive_path.stat().st_size != expected_size:
            raise RuntimeError("The managed engine download did not complete; retry to resume it")
        if sha256_file(self.archive_path) != expected_hash:
            self.archive_path.unlink(missing_ok=True)
            raise RuntimeError("The managed engine download failed verification")
        return self.archive_path

    def install(self, progress: Callable[[int, str], None]) -> None:
        self._cancel_install.clear()
        self._set_state("installing", "Downloading the reviewed local image engine")
        progress(5, self._message)
        archive = self._download()
        self._set_state("installing", "Verifying and extracting the local image engine")
        progress(45, self._message)
        staging = self.root.with_name(f"comfyui-staging-{uuid.uuid4().hex}")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            safe_extract_7z(archive, staging, self._verify_extractor())
            main = next(staging.rglob("main.py"), None)
            python = next(staging.rglob("python_embeded/python.exe"), None)
            if main is None or python is None:
                raise RuntimeError("The reviewed engine archive is missing its runtime files")
            wrapper = main.parent.parent
            if self.root.exists():
                shutil.rmtree(self.root)
            shutil.move(str(wrapper), str(self.root))
            self._write_extra_model_paths()
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        self._set_state("verifying", "Starting and verifying the local image engine")
        progress(80, self._message)
        self.start()
        if not self.wait_healthy(timeout=90):
            self._set_state("repair_needed", "The local image engine did not become ready")
            raise RuntimeError(self._message)
        self._set_state("ready", "Local Generation Engine is ready on this device")
        progress(100, self._message)

    def cancel_install(self) -> None:
        self._cancel_install.set()

    def mark_repair_needed(self, message: str) -> None:
        self._set_state("repair_needed", message)

    def _write_extra_model_paths(self) -> None:
        config = self.root / "ComfyUI" / "extra_model_paths.yaml"
        if not config.parent.is_dir():
            return
        config.write_text(
            "vanta:\n"
            f"  base_path: {self.settings.engine_root.as_posix()}\n"
            "  checkpoints: models/checkpoints\n"
            "  loras: models/loras\n"
            "  controlnet: models/controlnet\n"
            "  clip_vision: models/clip_vision\n"
            "  ipadapter: models/ipadapter\n"
            "  upscale_models: models/upscale_models\n",
            encoding="utf-8",
        )

    def start(self) -> None:
        with self._lock:
            self._refresh_process_state()
            if self._process is not None and self._process.poll() is None:
                return
            layout = self.installed_layout()
            if layout is None:
                self._state = "not_installed"
                self._message = "Local Generation Engine has not been installed"
                return
            main, python = layout
            self._write_extra_model_paths()
            port = allocate_loopback_port()
            logs = self.settings.logs_dir or self.settings.data_dir / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            stdout = (logs / "comfyui-stdout.log").open("ab")
            stderr = (logs / "comfyui-stderr.log").open("ab")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._process = subprocess.Popen(
                [
                    str(python),
                    "-s",
                    str(main),
                    "--listen",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--disable-auto-launch",
                    "--output-directory",
                    str(self.root / "output"),
                ],
                cwd=main.parent,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                creationflags=flags,
            )
            self._port = port
            self._state = "starting"
            self._message = "Starting the local image engine"
            logger.info("started managed ComfyUI revision=%s port=%s", self.revision, port)

    def stop(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self._process.kill()
            self._process = None
            self._port = None
            self._state = "stopped" if self.installed_layout() else "not_installed"
            self._message = "Local Generation Engine stopped"

    def _request_json(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        snapshot = self.snapshot()
        if snapshot.port is None:
            raise RuntimeError("The local image engine is not running")
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"http://127.0.0.1:{snapshot.port}{path}",
            data=data,
            headers={"Content-Type": "application/json"} if data else {},
            method="POST" if data else "GET",
        )
        with urlopen(request, timeout=20) as response:
            body = response.read()
            return json.loads(body) if body else {}

    def wait_healthy(self, timeout: int = 30) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self._request_json("/system_stats")
                self._restart_count = 0
                self._set_state("ready", "Local Generation Engine is ready on this device")
                return True
            except (OSError, RuntimeError, ValueError):
                time.sleep(0.5)
        return False

    def submit(
        self, workflow: dict[str, Any], on_progress: Callable[[int, int], None]
    ) -> tuple[str, dict[str, Any]]:
        snapshot = self.snapshot()
        if snapshot.state != "ready" or snapshot.port is None:
            raise RuntimeError("Install and start the local image engine before generating")
        client_id = f"vanta-{uuid.uuid4().hex}"
        ws_url = f"ws://127.0.0.1:{snapshot.port}/ws?clientId={client_id}"
        with connect(ws_url, open_timeout=10, close_timeout=2) as websocket:
            response = self._request_json("/prompt", {"prompt": workflow, "client_id": client_id})
            prompt_id = str(response.get("prompt_id", ""))
            if not prompt_id:
                raise RuntimeError(response.get("error", "The image engine rejected this workflow"))
            while True:
                try:
                    event = json.loads(websocket.recv(timeout=5))
                except TimeoutError:
                    event = {}
                event_type = event.get("type")
                data = event.get("data", {})
                if event_type == "progress" and data.get("prompt_id") == prompt_id:
                    maximum = max(1, int(data.get("max", 1)))
                    on_progress(int(data.get("value", 0)), maximum)
                if (
                    event_type == "executing"
                    and data.get("prompt_id") == prompt_id
                    and data.get("node") is None
                ):
                    break
        history = self._request_json(f"/history/{prompt_id}")
        record = history.get(prompt_id, {})
        if record.get("status", {}).get("status_str") != "success":
            messages = record.get("status", {}).get("messages", [])
            raise RuntimeError(f"The image engine could not finish this job: {messages}")
        return prompt_id, record

    def interrupt(self, prompt_id: str | None = None) -> None:
        try:
            if prompt_id:
                self._request_json("/queue", {"delete": [prompt_id]})
            self._request_json("/interrupt", {})
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
            logger.warning("unable to interrupt ComfyUI job", exc_info=True)
