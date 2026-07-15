from __future__ import annotations

import hashlib
import http.client
import os
import shutil
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


class DownloadCancelled(RuntimeError):
    pass


class DownloadPaused(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    state: str
    stage: str
    downloaded_bytes: int
    expected_bytes: int | None
    speed_bytes_per_second: float | None
    elapsed_seconds: float
    eta_seconds: float | None
    resumable: bool
    final_url: str | None = None

    @property
    def percentage(self) -> int | None:
        if not self.expected_bytes:
            return None
        return min(100, int(self.downloaded_bytes * 100 / self.expected_bytes))


ProgressCallback = Callable[[DownloadProgress], None]
FlagCallback = Callable[[], bool]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def revalidate_huggingface_file(
    url: str,
    *,
    repository: str,
    revision: str,
    filename: str,
    expected_bytes: int,
    expected_sha256: str,
    user_agent: str = "Vanta/0.1.3",
) -> dict[str, str | int]:
    """Confirm Hugging Face's live immutable-file metadata still matches the reviewed manifest."""
    parsed = urlparse(url)
    expected_path = f"/{repository}/resolve/{revision}/{filename}"
    if (
        parsed.scheme != "https"
        or parsed.hostname != "huggingface.co"
        or parsed.path != expected_path
    ):
        raise RuntimeError("The reviewed model URL no longer matches its pinned repository file")
    request = Request(url, headers={"User-Agent": user_agent}, method="HEAD")
    try:
        response = build_opener(_NoRedirect()).open(request, timeout=30)
        headers = response.headers
        location = response.geturl()
        response.close()
    except HTTPError as error:
        if error.code not in {301, 302, 303, 307, 308}:
            raise RuntimeError(f"Live model metadata returned HTTP {error.code}") from error
        headers = error.headers
        location = headers.get("Location", "")
    linked_revision = str(headers.get("X-Repo-Commit", ""))
    linked_size = str(headers.get("X-Linked-Size", ""))
    linked_hash = str(headers.get("X-Linked-Etag", "")).strip('"')
    if linked_revision != revision:
        raise RuntimeError("The live model revision does not match Vanta's reviewed manifest")
    if linked_size != str(expected_bytes):
        raise RuntimeError("The live model byte count does not match Vanta's reviewed manifest")
    if linked_hash.lower() != expected_sha256.lower():
        raise RuntimeError("The live model hash does not match Vanta's reviewed manifest")
    if not location:
        raise RuntimeError("The reviewed model provider did not return an approved file location")
    StreamDownloader._validate_url(location)
    StreamDownloader._validate_redirect(url, location)
    with urlopen(request, timeout=30) as final:
        final_url = final.geturl()
        StreamDownloader._validate_url(final_url)
        StreamDownloader._validate_redirect(url, final_url)
        content_length = final.headers.get("Content-Length")
        if content_length and int(content_length) != expected_bytes:
            raise RuntimeError(
                "The live model response length does not match the reviewed manifest"
            )
    return {
        "repository": repository,
        "revision": linked_revision,
        "filename": filename,
        "bytes": expected_bytes,
        "sha256": linked_hash.lower(),
        "resolved_host": urlparse(final_url).hostname or "",
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class StreamDownloader:
    """A resumable streaming downloader whose progress is derived only from bytes on disk."""

    def __init__(self, *, user_agent: str = "Vanta/0.1.3", chunk_size: int = 1024 * 1024):
        self.user_agent = user_agent
        self.chunk_size = chunk_size

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urlparse(url)
        local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
        if parsed.scheme != "https" and not local_http:
            raise RuntimeError("The reviewed download redirected to an unapproved transport")

    @staticmethod
    def _validate_redirect(source_url: str, final_url: str) -> None:
        source = urlparse(source_url)
        final = urlparse(final_url)
        if source.hostname in {"127.0.0.1", "localhost"}:
            if final.hostname not in {"127.0.0.1", "localhost"}:
                raise RuntimeError("The local test download redirected outside loopback")
            return
        source_host = (source.hostname or "").lower()
        final_host = (final.hostname or "").lower()
        approved = {source_host}
        if source_host in {"github.com", "api.github.com"}:
            approved.update(
                {
                    "objects.githubusercontent.com",
                    "release-assets.githubusercontent.com",
                    "github-releases.githubusercontent.com",
                }
            )
        if source_host == "huggingface.co" or source_host.endswith(".huggingface.co"):
            approved.update(
                {
                    "cdn-lfs.huggingface.co",
                    "cas-bridge.xethub.hf.co",
                    "cas-server.xethub.hf.co",
                }
            )
        if final_host not in approved:
            raise RuntimeError(f"The reviewed download redirected to unapproved host {final_host}")

    @staticmethod
    def _content_range_start(value: str | None) -> int | None:
        if not value or not value.lower().startswith("bytes "):
            return None
        try:
            return int(value.split(" ", 1)[1].split("-", 1)[0])
        except (IndexError, ValueError):
            return None

    def download(
        self,
        url: str,
        destination: Path,
        *,
        partial_path: Path,
        expected_bytes: int | None,
        expected_sha256: str,
        on_progress: ProgressCallback,
        cancel_requested: FlagCallback = lambda: False,
        pause_requested: FlagCallback = lambda: False,
        max_attempts: int = 5,
        minimum_free_bytes: int = 64 * 1024 * 1024,
    ) -> Path:
        self._validate_url(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial_path.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file():
            size_ok = expected_bytes is None or destination.stat().st_size == expected_bytes
            if size_ok and sha256_file(destination) == expected_sha256:
                size = destination.stat().st_size
                on_progress(
                    DownloadProgress(
                        "verifying_download",
                        "Verified existing download",
                        size,
                        expected_bytes or size,
                        None,
                        0,
                        0,
                        True,
                        url,
                    )
                )
                return destination
        if (
            expected_bytes
            and partial_path.exists()
            and partial_path.stat().st_size > expected_bytes
        ):
            partial_path.unlink()

        started = time.monotonic()
        samples: deque[tuple[float, int]] = deque(maxlen=12)
        last_emit = 0.0
        final_url: str | None = None

        def emit(state: str, stage: str, downloaded: int, resumable: bool) -> None:
            nonlocal last_emit
            now = time.monotonic()
            samples.append((now, downloaded))
            while len(samples) > 2 and now - samples[0][0] > 12:
                samples.popleft()
            speed: float | None = None
            if len(samples) >= 2:
                duration = samples[-1][0] - samples[0][0]
                delta = samples[-1][1] - samples[0][1]
                if duration > 0 and delta >= 0:
                    speed = delta / duration
            eta = None
            if speed and expected_bytes and downloaded < expected_bytes:
                eta = (expected_bytes - downloaded) / speed
            on_progress(
                DownloadProgress(
                    state,
                    stage,
                    downloaded,
                    expected_bytes,
                    speed,
                    now - started,
                    eta,
                    resumable,
                    final_url,
                )
            )
            last_emit = now

        attempt = 0
        while attempt < max_attempts:
            if cancel_requested():
                raise DownloadCancelled("Download cancelled; partial bytes were preserved")
            existing = partial_path.stat().st_size if partial_path.exists() else 0
            while pause_requested():
                emit("paused", "Paused", existing, existing > 0)
                if cancel_requested():
                    raise DownloadCancelled("Download cancelled; partial bytes were preserved")
                time.sleep(0.25)
            headers = {"User-Agent": self.user_agent, "Accept": "application/octet-stream"}
            if existing:
                headers["Range"] = f"bytes={existing}-"
            emit("connecting", "Connecting", existing, existing > 0)
            try:
                with urlopen(Request(url, headers=headers), timeout=45) as response:
                    final_url = response.geturl()
                    self._validate_url(final_url)
                    self._validate_redirect(url, final_url)
                    status = int(getattr(response, "status", response.getcode()))
                    resumed = (
                        existing > 0
                        and status == 206
                        and self._content_range_start(response.headers.get("Content-Range"))
                        == existing
                    )
                    mode = "ab" if resumed else "wb"
                    written = existing if resumed else 0
                    declared_length = response.headers.get("Content-Length")
                    if expected_bytes is not None and declared_length:
                        expected_response_bytes = expected_bytes - written
                        if int(declared_length) != expected_response_bytes:
                            raise RuntimeError(
                                f"Server declared {declared_length} bytes; expected {expected_response_bytes} bytes for this response"
                            )
                    with partial_path.open(mode) as target:
                        while True:
                            if cancel_requested():
                                target.flush()
                                os.fsync(target.fileno())
                                raise DownloadCancelled(
                                    "Download cancelled; partial bytes were preserved"
                                )
                            if pause_requested():
                                target.flush()
                                os.fsync(target.fileno())
                                emit("paused", "Paused", written, True)
                                while pause_requested():
                                    if cancel_requested():
                                        raise DownloadCancelled(
                                            "Download cancelled; partial bytes were preserved"
                                        )
                                    time.sleep(0.25)
                                # Reconnect so a long pause never relies on a stale CDN socket.
                                raise DownloadPaused("Resume with a validated Range request")
                            chunk = response.read(self.chunk_size)
                            if not chunk:
                                break
                            if (
                                shutil.disk_usage(partial_path.parent).free
                                < len(chunk) + minimum_free_bytes
                            ):
                                raise RuntimeError(
                                    "The selected storage location ran out of space during download"
                                )
                            target.write(chunk)
                            written += len(chunk)
                            now = time.monotonic()
                            if now - last_emit >= 0.35:
                                target.flush()
                                emit("downloading", destination.name, written, True)
                        target.flush()
                        os.fsync(target.fileno())
                    emit("downloading", destination.name, written, True)
                if expected_bytes is not None and written != expected_bytes:
                    attempt += 1
                    if attempt >= max_attempts:
                        raise RuntimeError(
                            f"Download ended at {written} bytes; expected {expected_bytes} bytes"
                        )
                    time.sleep(min(2 ** (attempt - 1), 20))
                    continue
                emit("verifying_download", "Verifying SHA-256", written, True)
                actual_hash = sha256_file(partial_path)
                if actual_hash.lower() != expected_sha256.lower():
                    raise RuntimeError(
                        f"Downloaded file hash mismatch: expected {expected_sha256}, got {actual_hash}"
                    )
                os.replace(partial_path, destination)
                emit("verifying_download", "Download verified", written, True)
                return destination
            except DownloadCancelled:
                raise
            except DownloadPaused:
                continue
            except HTTPError as error:
                if error.code not in {408, 429, 500, 502, 503, 504}:
                    raise RuntimeError(f"Download failed with HTTP {error.code}") from error
                retry_after = error.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
            except (
                URLError,
                TimeoutError,
                ConnectionError,
                OSError,
                http.client.IncompleteRead,
                http.client.RemoteDisconnected,
            ) as error:
                delay = 2**attempt
                if attempt + 1 >= max_attempts:
                    raise RuntimeError(f"Download connection failed: {error}") from error
            except RuntimeError:
                raise
            attempt += 1
            if attempt >= max_attempts:
                break
            time.sleep(min(delay, 20))
        raise RuntimeError("Download could not be completed after bounded retries")
