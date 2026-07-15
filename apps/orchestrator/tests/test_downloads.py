from __future__ import annotations

import hashlib
import threading
import time
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.error import HTTPError

import pytest

from vanta_orchestrator.downloads import (
    DownloadCancelled,
    DownloadProgress,
    StreamDownloader,
    revalidate_huggingface_file,
)

PAYLOAD = bytes(range(256)) * 32768
PAYLOAD_HASH = hashlib.sha256(PAYLOAD).hexdigest()


class DownloadHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    interrupted = False

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/full")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path in {"/403", "/404", "/429", "/500"}:
            code = int(self.path[1:])
            self.send_response(code)
            self.send_header("Content-Length", "0")
            if code == 429:
                self.send_header("Retry-After", "0")
            self.end_headers()
            return
        start = 0
        range_header = self.headers.get("Range")
        range_supported = self.path != "/ignore-range"
        if range_header and range_supported:
            start = int(range_header.removeprefix("bytes=").split("-", 1)[0])
        body = PAYLOAD[start:]
        self.send_response(206 if start else 200)
        if start:
            self.send_header("Content-Range", f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}")
        declared = len(body) + (17 if self.path == "/wrong-length" else 0)
        self.send_header("Content-Length", str(declared))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        limit = len(body)
        if self.path == "/early-eof":
            limit //= 2
        if self.path == "/interrupt" and not type(self).interrupted:
            type(self).interrupted = True
            limit //= 2
        step = 65536
        for offset in range(0, limit, step):
            try:
                self.wfile.write(body[offset : min(limit, offset + step)])
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                break
            if self.path == "/slow":
                time.sleep(0.01)
        if limit != len(body):
            self.close_connection = True


@pytest.fixture()
def download_server() -> str:
    DownloadHandler.interrupted = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), DownloadHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def run_download(
    tmp_path: Path,
    url: str,
    *,
    cancel=lambda: False,
    pause=lambda: False,
    **download_options: Any,
) -> tuple[Path, Path, list[DownloadProgress]]:
    destination = tmp_path / "payload.bin"
    partial = tmp_path / "payload.bin.partial"
    progress: list[DownloadProgress] = []
    StreamDownloader(chunk_size=65536).download(
        url,
        destination,
        partial_path=partial,
        expected_bytes=len(PAYLOAD),
        expected_sha256=PAYLOAD_HASH,
        on_progress=progress.append,
        cancel_requested=cancel,
        pause_requested=pause,
        **download_options,
    )
    return destination, partial, progress


@pytest.mark.parametrize("endpoint", ["full", "redirect"])
def test_real_streamed_transfer_uses_file_backed_byte_progress(
    tmp_path: Path, download_server: str, endpoint: str
) -> None:
    destination, partial, progress = run_download(tmp_path, f"{download_server}/{endpoint}")
    assert destination.read_bytes() == PAYLOAD
    assert not partial.exists()
    assert progress[-1].downloaded_bytes == destination.stat().st_size == len(PAYLOAD)
    assert progress[-1].percentage == 100
    assert any(item.speed_bytes_per_second for item in progress)


def test_range_resume_and_server_ignoring_range_are_both_safe(
    tmp_path: Path, download_server: str
) -> None:
    partial = tmp_path / "payload.bin.partial"
    partial.write_bytes(PAYLOAD[:1_000_000])
    destination, _, progress = run_download(tmp_path, f"{download_server}/full")
    assert destination.read_bytes() == PAYLOAD
    assert progress[-1].downloaded_bytes == len(PAYLOAD)

    destination.unlink()
    partial.write_bytes(PAYLOAD[:1_000_000])
    destination, _, _ = run_download(tmp_path, f"{download_server}/ignore-range")
    assert destination.read_bytes() == PAYLOAD


def test_interrupted_connection_reconnects_with_range(tmp_path: Path, download_server: str) -> None:
    destination, _, _ = run_download(tmp_path, f"{download_server}/interrupt")
    assert destination.read_bytes() == PAYLOAD


def test_pause_stops_growth_and_resume_continues(tmp_path: Path, download_server: str) -> None:
    paused = threading.Event()
    destination = tmp_path / "payload.bin"
    partial = tmp_path / "payload.bin.partial"
    errors: list[BaseException] = []

    def work() -> None:
        try:
            run_download(tmp_path, f"{download_server}/slow", pause=paused.is_set)
        except BaseException as error:  # pragma: no cover - asserted below
            errors.append(error)

    worker = threading.Thread(target=work)
    worker.start()
    deadline = time.monotonic() + 5
    while (not partial.exists() or partial.stat().st_size < 262144) and time.monotonic() < deadline:
        time.sleep(0.01)
    paused.set()
    time.sleep(0.15)
    size = partial.stat().st_size
    time.sleep(0.2)
    assert partial.stat().st_size == size
    paused.clear()
    worker.join(timeout=10)
    assert not errors and destination.read_bytes() == PAYLOAD


def test_cancel_preserves_partial_and_never_creates_final(
    tmp_path: Path, download_server: str
) -> None:
    cancelled = threading.Event()
    partial = tmp_path / "payload.bin.partial"

    def cancel_after_growth() -> bool:
        if partial.exists() and partial.stat().st_size >= 262144:
            cancelled.set()
        return cancelled.is_set()

    with pytest.raises(DownloadCancelled):
        run_download(tmp_path, f"{download_server}/slow", cancel=cancel_after_growth)
    assert partial.is_file() and partial.stat().st_size > 0
    assert not (tmp_path / "payload.bin").exists()


@pytest.mark.parametrize("endpoint", ["403", "404", "429", "500", "early-eof", "wrong-length"])
def test_transport_and_length_failures_remain_incomplete(
    tmp_path: Path, download_server: str, endpoint: str
) -> None:
    with pytest.raises(RuntimeError):
        run_download(tmp_path, f"{download_server}/{endpoint}", max_attempts=2)
    assert not (tmp_path / "payload.bin").exists()


def test_hash_mismatch_never_activates_the_file(tmp_path: Path, download_server: str) -> None:
    destination = tmp_path / "payload.bin"
    partial = tmp_path / "payload.bin.partial"
    with pytest.raises(RuntimeError, match="hash mismatch"):
        StreamDownloader().download(
            f"{download_server}/full",
            destination,
            partial_path=partial,
            expected_bytes=len(PAYLOAD),
            expected_sha256="0" * 64,
            on_progress=lambda _progress: None,
        )
    assert partial.stat().st_size == len(PAYLOAD)
    assert not destination.exists()


def test_insufficient_space_stops_before_writing_a_chunk(
    tmp_path: Path, download_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "vanta_orchestrator.downloads.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=0),
    )
    with pytest.raises(RuntimeError, match="ran out of space"):
        run_download(tmp_path, f"{download_server}/full", minimum_free_bytes=0)
    assert not (tmp_path / "payload.bin").exists()


def test_live_huggingface_metadata_must_match_the_pinned_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "a" * 40
    digest = "b" * 64
    source = f"https://huggingface.co/owner/repo/resolve/{revision}/model.safetensors"
    location = "https://cas-bridge.xethub.hf.co/blob"
    headers = Message()
    headers["Location"] = location
    headers["X-Repo-Commit"] = revision
    headers["X-Linked-Size"] = "123"
    headers["X-Linked-Etag"] = f'"{digest}"'

    class Opener:
        def open(self, request: Any, timeout: int) -> None:
            raise HTTPError(request.full_url, 302, "Found", headers, None)

    class FinalResponse:
        def __init__(self) -> None:
            self.headers = Message()
            self.headers["Content-Length"] = "123"

        def __enter__(self) -> FinalResponse:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def geturl(self) -> str:
            return location

    monkeypatch.setattr("vanta_orchestrator.downloads.build_opener", lambda *_args: Opener())
    monkeypatch.setattr(
        "vanta_orchestrator.downloads.urlopen", lambda *_args, **_kwargs: FinalResponse()
    )
    result = revalidate_huggingface_file(
        source,
        repository="owner/repo",
        revision=revision,
        filename="model.safetensors",
        expected_bytes=123,
        expected_sha256=digest,
    )
    assert result["resolved_host"] == "cas-bridge.xethub.hf.co"

    headers.replace_header("X-Linked-Size", "124")
    with pytest.raises(RuntimeError, match="byte count"):
        revalidate_huggingface_file(
            source,
            repository="owner/repo",
            revision=revision,
            filename="model.safetensors",
            expected_bytes=123,
            expected_sha256=digest,
        )
