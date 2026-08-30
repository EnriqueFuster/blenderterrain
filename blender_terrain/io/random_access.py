"""Bounded random access to local files and trusted HTTPS range sources."""

from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..errors import DownloadIntegrityError, NoCoverageError, ProviderUnavailableError
from .atomic import finalize_part

_CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)")


class RandomAccessReader(Protocol):
    """Read exact byte ranges from a finite immutable object."""

    @property
    def size(self) -> int: ...

    def read(self, offset: int, length: int) -> bytes: ...


class RangeOpener(Protocol):
    def open(self, request: Request, timeout: float) -> Any: ...


@dataclass(frozen=True, slots=True)
class LocalRandomAccessReader:
    """Random access backed by one local file."""

    path: Path
    size: int

    @classmethod
    def open(cls, path: Path) -> LocalRandomAccessReader:
        return cls(path, path.stat().st_size)

    def read(self, offset: int, length: int) -> bytes:
        _validate_range(offset, length, self.size)
        with self.path.open("rb") as stream:
            stream.seek(offset)
            payload = stream.read(length)
        if len(payload) != length:
            raise DownloadIntegrityError("Local random-access read was truncated")
        return payload


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class HttpRangeReader:
    """Read and cache fixed-size blocks from one allowlisted HTTPS object."""

    def __init__(
        self,
        url: str,
        cache_directory: Path,
        *,
        allowed_hosts: frozenset[str],
        maximum_source_bytes: int,
        block_bytes: int = 65_536,
        timeout_seconds: float = 30.0,
        opener: RangeOpener | None = None,
    ) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in allowed_hosts
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Remote raster URL is not an allowlisted HTTPS source")
        if maximum_source_bytes <= 0 or block_bytes <= 0 or timeout_seconds <= 0:
            raise ValueError("Remote random-access limits must be positive")
        self.url = url
        self._cache_directory = cache_directory / hashlib.sha256(url.encode()).hexdigest()
        self._maximum_source_bytes = maximum_source_bytes
        self._block_bytes = block_bytes
        self._timeout_seconds = timeout_seconds
        self._opener = opener or build_opener(_NoRedirects())
        first, total = self._fetch(0, block_bytes)
        self.size = total
        self._store_block(0, first)

    def read(self, offset: int, length: int) -> bytes:
        _validate_range(offset, length, self.size)
        first_block = offset // self._block_bytes
        last_block = (offset + length - 1) // self._block_bytes
        indices = tuple(range(first_block, last_block + 1))
        with ThreadPoolExecutor(max_workers=min(4, len(indices))) as executor:
            blocks = tuple(executor.map(self._read_block, indices))
        payload = bytearray().join(blocks)
        relative = offset - first_block * self._block_bytes
        return bytes(payload[relative : relative + length])

    def _read_block(self, block_index: int) -> bytes:
        path = self._block_path(block_index)
        expected = min(self._block_bytes, self.size - block_index * self._block_bytes)
        if path.is_file() and path.stat().st_size == expected:
            return path.read_bytes()
        payload, total = self._fetch(block_index * self._block_bytes, expected)
        if total != self.size:
            raise DownloadIntegrityError("Remote raster size changed during range access")
        self._store_block(block_index, payload)
        return payload

    def _fetch(self, offset: int, length: int) -> tuple[bytes, int]:
        request = Request(
            self.url,
            headers={
                "User-Agent": "BlenderTerrain/0.4",
                "Range": f"bytes={offset}-{offset + length - 1}",
                "Accept-Encoding": "identity",
            },
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                status = getattr(response, "status", None)
                content_range = response.headers.get("Content-Range", "")
                match = _CONTENT_RANGE.fullmatch(content_range)
                payload = response.read(length + 1)
        except HTTPError as exc:
            if exc.code == 404:
                raise NoCoverageError("Remote raster has no data for this area") from None
            raise ProviderUnavailableError(
                f"Remote raster range request returned HTTP {exc.code}"
            ) from None
        except OSError as exc:
            raise ProviderUnavailableError("Remote raster range request failed") from exc
        if status != 206 or match is None:
            raise DownloadIntegrityError("Remote raster source did not honor HTTP Range")
        start, end, total = (int(value) for value in match.groups())
        expected = min(length, total - offset)
        if (
            start != offset
            or end != offset + expected - 1
            or len(payload) != expected
            or total > self._maximum_source_bytes
        ):
            raise DownloadIntegrityError("Remote raster returned an invalid byte range")
        return payload, total

    def _store_block(self, block_index: int, payload: bytes) -> None:
        path = self._block_path(block_index)
        if path.is_file() and path.stat().st_size == len(payload):
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.unlink(missing_ok=True)
        part.write_bytes(payload)
        finalize_part(part, path)

    def _block_path(self, block_index: int) -> Path:
        return self._cache_directory / f"{block_index:012d}.bin"


def _validate_range(offset: int, length: int, size: int) -> None:
    if offset < 0 or length <= 0 or offset + length > size:
        raise ValueError("Random-access range is outside the source")
