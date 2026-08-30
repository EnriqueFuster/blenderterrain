"""Bounded cached downloads for trusted public raster assets."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..errors import (
    DownloadIntegrityError,
    JobCancelled,
    NoCoverageError,
    ProviderUnavailableError,
)
from .atomic import finalize_part, safe_destination
from .tiff_validation import validate_tiff_header

_CHUNK_SIZE = 1024 * 1024
_TIFF_CONTENT_TYPES = {
    "application/geotiff",
    "application/octet-stream",
    "application/tiff",
    "binary/octet-stream",
    "image/tif",
    "image/tiff",
}
_RETRYABLE_HTTP_STATUS = {408, 429, 500, 502, 503, 504}


class _Response(Protocol):
    headers: Message

    def read(self, amount: int = -1) -> bytes: ...

    def __enter__(self) -> _Response: ...

    def __exit__(self, *args: object) -> None: ...


class _Opener(Protocol):
    def open(self, request: Request, timeout: float) -> _Response: ...


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class _RetryableDownloadError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class DownloadedAsset:
    path: Path
    bytes: int
    cached: bool


def download_public_tiff(
    url: str,
    cache_directory: Path,
    filename: str,
    *,
    maximum_bytes: int,
    timeout_seconds: float = 30.0,
    retries: int = 2,
    progress_callback: Callable[[int, int | None], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    opener: _Opener | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    not_found_is_no_coverage: bool = False,
) -> DownloadedAsset:
    """Download one direct HTTPS TIFF or reuse a structurally valid cached file."""

    _validate_request(url, maximum_bytes, timeout_seconds, retries)
    cache_directory.mkdir(parents=True, exist_ok=True)
    destination = safe_destination(cache_directory, filename)
    if destination.exists():
        size = destination.stat().st_size
        if size > maximum_bytes:
            raise DownloadIntegrityError("Cached raster exceeds the configured byte limit")
        validate_tiff_header(destination)
        return DownloadedAsset(destination, size, True)

    part_path = destination.with_name(destination.name + ".part")
    part_path.unlink(missing_ok=True)
    client = opener or cast(_Opener, build_opener(_NoRedirects()))
    for attempt in range(retries + 1):
        try:
            written = _download_once(
                client,
                url,
                part_path,
                maximum_bytes,
                timeout_seconds,
                progress_callback,
                cancelled,
                not_found_is_no_coverage,
            )
            validate_tiff_header(part_path)
            finalize_part(part_path, destination)
            return DownloadedAsset(destination, written, False)
        except _RetryableDownloadError as exc:
            part_path.unlink(missing_ok=True)
            if attempt == retries:
                raise ProviderUnavailableError(
                    f"Public raster download failed after {attempt + 1} attempts"
                ) from exc
            sleeper(0.25 * (2**attempt))
        except Exception:
            part_path.unlink(missing_ok=True)
            raise
    raise AssertionError("Download retry loop did not terminate")


def _download_once(
    opener: _Opener,
    url: str,
    part_path: Path,
    maximum_bytes: int,
    timeout_seconds: float,
    progress_callback: Callable[[int, int | None], None] | None,
    cancelled: Callable[[], bool] | None,
    not_found_is_no_coverage: bool,
) -> int:
    request = Request(
        url,
        headers={"User-Agent": "BlenderTerrain/0.4", "Accept": "image/tiff"},
    )
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            declared_bytes = _declared_size(response.headers, maximum_bytes)
            content_type = response.headers.get_content_type().lower()
            if content_type not in _TIFF_CONTENT_TYPES:
                raise DownloadIntegrityError("Raster source returned an unsupported content type")
            written = 0
            with part_path.open("xb") as stream:
                while True:
                    if cancelled is not None and cancelled():
                        raise JobCancelled("Public raster download was cancelled")
                    chunk = response.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > maximum_bytes:
                        raise DownloadIntegrityError(
                            "Downloaded raster exceeds the configured byte limit"
                        )
                    stream.write(chunk)
                    if progress_callback is not None:
                        progress_callback(written, declared_bytes)
            if declared_bytes is not None and written != declared_bytes:
                raise DownloadIntegrityError(
                    "Downloaded raster size does not match Content-Length"
                )
            return written
    except HTTPError as exc:
        if exc.code == 404 and not_found_is_no_coverage:
            raise NoCoverageError("Raster source has no data for this area") from None
        if 300 <= exc.code < 400:
            raise DownloadIntegrityError("Raster source attempted a redirect") from None
        if exc.code in _RETRYABLE_HTTP_STATUS:
            raise _RetryableDownloadError from exc
        raise ProviderUnavailableError(f"Raster source returned HTTP {exc.code}") from None
    except (URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise _RetryableDownloadError from exc


def _declared_size(headers: Message, maximum_bytes: int) -> int | None:
    value = headers.get("Content-Length")
    if value is None:
        return None
    try:
        declared = int(value)
    except ValueError as exc:
        raise DownloadIntegrityError("Content-Length is not an integer") from exc
    if declared < 0 or declared > maximum_bytes:
        raise DownloadIntegrityError("Declared raster size exceeds the configured byte limit")
    return declared


def _validate_request(
    url: str, maximum_bytes: int, timeout_seconds: float, retries: int
) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Public raster URL must be an HTTPS URL without credentials")
    if maximum_bytes <= 0 or timeout_seconds <= 0 or retries < 0:
        raise ValueError("Download limits, timeout, and retry count are invalid")
