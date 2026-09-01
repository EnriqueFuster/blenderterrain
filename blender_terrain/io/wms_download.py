"""Atomic bounded downloads shared by WMS raster providers."""

from __future__ import annotations

import time
from collections.abc import Callable
from email.message import Message
from pathlib import Path
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..errors import (
    DownloadIntegrityError,
    JobCancelled,
    ProviderUnavailableError,
)
from .atomic import finalize_part, safe_destination

RETRYABLE_HTTP_STATUS = {408, 429, 500, 502, 503, 504}


class WMSResponse(Protocol):
    headers: Message

    def read(self, amount: int = -1) -> bytes: ...

    def __enter__(self) -> WMSResponse: ...

    def __exit__(self, *args: object) -> None: ...


class WMSOpener(Protocol):
    def open(self, request: Request, timeout: float) -> WMSResponse: ...


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class _RetryableRequest(Exception):
    def __init__(self, retry_after: float | None = None) -> None:
        self.retry_after = retry_after


def build_wms_opener() -> WMSOpener:
    return cast(WMSOpener, build_opener(_NoRedirects()))


def download_wms_response(
    url: str,
    cache_directory: Path,
    filename: str,
    *,
    content_type: str,
    accept: str | None = None,
    maximum_bytes: int,
    exact_bytes: int | None = None,
    timeout_seconds: float = 30.0,
    retries: int = 2,
    validator: Callable[[Path], None] | None = None,
    progress_callback: Callable[[int, int | None], None] | None = None,
    cancellation_requested: Callable[[], bool] = lambda: False,
    opener: WMSOpener | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> Path:
    """Download one WMS response and atomically publish it after validation."""

    if maximum_bytes <= 0 or timeout_seconds <= 0 or retries < 0:
        raise ValueError("WMS download limits are invalid")
    cache_directory.mkdir(parents=True, exist_ok=True)
    destination = safe_destination(cache_directory, filename)
    if destination.exists():
        raise DownloadIntegrityError("WMS destination already exists")
    part = destination.with_name(destination.name + ".part")
    part.unlink(missing_ok=True)
    client = opener or build_wms_opener()
    wait = sleeper or time.sleep
    for attempt in range(retries + 1):
        try:
            _download_once(
                client,
                url,
                part,
                content_type,
                accept or content_type,
                maximum_bytes,
                exact_bytes,
                timeout_seconds,
                progress_callback,
                cancellation_requested,
            )
            if validator is not None:
                validator(part)
            finalize_part(part, destination)
            return destination
        except _RetryableRequest as exc:
            part.unlink(missing_ok=True)
            if attempt == retries:
                raise ProviderUnavailableError(
                    f"WMS request failed after {attempt + 1} attempts"
                ) from exc
            wait(exc.retry_after or 0.25 * (2**attempt))
        except BaseException:
            part.unlink(missing_ok=True)
            raise
    raise AssertionError("WMS retry loop did not terminate")


def _download_once(
    opener: WMSOpener,
    url: str,
    part: Path,
    content_type: str,
    accept: str,
    maximum_bytes: int,
    exact_bytes: int | None,
    timeout_seconds: float,
    progress_callback: Callable[[int, int | None], None] | None,
    cancellation_requested: Callable[[], bool],
) -> None:
    request = Request(
        url,
        headers={"User-Agent": "BlenderTerrain/0.5", "Accept": accept},
    )
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            if response.headers.get_content_type().lower() != content_type:
                raise DownloadIntegrityError("WMS returned an unexpected content type")
            declared = _content_length(response.headers)
            if declared is not None and (
                declared > maximum_bytes
                or (exact_bytes is not None and declared != exact_bytes)
            ):
                raise DownloadIntegrityError("WMS Content-Length does not match its limit")
            written = 0
            with part.open("xb") as stream:
                while chunk := response.read(1024 * 1024):
                    if cancellation_requested():
                        raise JobCancelled("WMS acquisition was cancelled")
                    written += len(chunk)
                    if written > maximum_bytes:
                        raise DownloadIntegrityError("WMS response exceeds its byte limit")
                    stream.write(chunk)
                    if progress_callback is not None:
                        progress_callback(written, exact_bytes or declared)
            if (declared is not None and written != declared) or (
                exact_bytes is not None and written != exact_bytes
            ):
                raise DownloadIntegrityError("WMS response is incomplete")
    except HTTPError as exc:
        if exc.code in RETRYABLE_HTTP_STATUS:
            raise _RetryableRequest(_retry_after(exc.headers)) from exc
        raise ProviderUnavailableError(f"WMS returned HTTP {exc.code}") from None
    except (URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise _RetryableRequest from exc


def _content_length(headers: Message) -> int | None:
    value = headers.get("Content-Length")
    if value is None:
        return None
    try:
        length = int(value)
    except ValueError as exc:
        raise DownloadIntegrityError("WMS Content-Length is not an integer") from exc
    if length < 0:
        raise DownloadIntegrityError("WMS Content-Length cannot be negative")
    return length


def _retry_after(headers: Message) -> float | None:
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if 0.0 <= seconds <= 60.0 else None
