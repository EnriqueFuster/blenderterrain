from __future__ import annotations

from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from blender_terrain.errors import DownloadIntegrityError, NoCoverageError
from blender_terrain.io.random_access import HttpRangeReader, RandomAccessIO


class RangeResponse(BytesIO):
    def __init__(self, payload: bytes, start: int, total: int, status: int = 206) -> None:
        super().__init__(payload)
        self.status = status
        self.headers = Message()
        self.headers["Content-Range"] = f"bytes {start}-{start + len(payload) - 1}/{total}"

    def __enter__(self) -> RangeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class BytesRangeOpener:
    def __init__(self, payload: bytes, *, honor_range: bool = True) -> None:
        self.payload = payload
        self.honor_range = honor_range
        self.calls: list[tuple[int, int]] = []

    def open(self, request: Request, timeout: float) -> RangeResponse:
        value = request.get_header("Range")
        assert value is not None
        start_text, end_text = value.removeprefix("bytes=").split("-")
        start, end = int(start_text), min(int(end_text), len(self.payload) - 1)
        self.calls.append((start, end))
        status = 206 if self.honor_range else 200
        return RangeResponse(self.payload[start : end + 1], start, len(self.payload), status)


def test_exposes_random_access_reader_as_seekable_stream() -> None:
    payload = b"0123456789"
    source = type(
        "MemoryReader",
        (),
        {
            "size": len(payload),
            "read": lambda self, offset, length: payload[offset : offset + length],
        },
    )()
    stream = RandomAccessIO(source)

    assert stream.read(3) == b"012"
    assert stream.seek(-2, 2) == 8
    assert stream.read() == b"89"
    assert stream.seek(4) == 4
    assert stream.read(2) == b"45"


def test_reads_across_cached_http_blocks(tmp_path: Path) -> None:
    payload = bytes(range(100))
    opener = BytesRangeOpener(payload)
    reader = HttpRangeReader(
        "https://data.example.test/global.tif",
        tmp_path,
        allowed_hosts=frozenset({"data.example.test"}),
        maximum_source_bytes=1_000,
        block_bytes=16,
        opener=opener,
    )

    assert reader.size == 100
    assert reader.read(12, 12) == payload[12:24]
    assert reader.read(12, 12) == payload[12:24]
    assert opener.calls == [(0, 15), (16, 31)]


def test_stable_cache_key_reuses_blocks_across_signed_urls(tmp_path: Path) -> None:
    payload = bytes(range(32))
    first_opener = BytesRangeOpener(payload)
    first = HttpRangeReader(
        "https://data.example.test/global.tif?signature=first",
        tmp_path,
        allowed_hosts=frozenset({"data.example.test"}),
        maximum_source_bytes=1_000,
        block_bytes=16,
        opener=first_opener,
        cache_key="official-resource",
    )
    assert first.read(0, 16) == payload[:16]

    second_opener = BytesRangeOpener(payload)
    second = HttpRangeReader(
        "https://data.example.test/global.tif?signature=second",
        tmp_path,
        allowed_hosts=frozenset({"data.example.test"}),
        maximum_source_bytes=1_000,
        block_bytes=16,
        opener=second_opener,
        cache_key="official-resource",
    )
    assert second.read(0, 16) == payload[:16]
    assert second_opener.calls == [(0, 15)]


def test_rejects_servers_that_ignore_range(tmp_path: Path) -> None:
    with pytest.raises(DownloadIntegrityError, match="did not honor"):
        HttpRangeReader(
            "https://data.example.test/global.tif",
            tmp_path,
            allowed_hosts=frozenset({"data.example.test"}),
            maximum_source_bytes=1_000,
            opener=BytesRangeOpener(b"remote raster", honor_range=False),
        )


def test_rejects_untrusted_hosts(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="allowlisted"):
        HttpRangeReader(
            "https://untrusted.example/global.tif",
            tmp_path,
            allowed_hosts=frozenset({"data.example.test"}),
            maximum_source_bytes=1_000,
        )


def test_reports_a_missing_remote_object_as_no_coverage(tmp_path: Path) -> None:
    class MissingOpener:
        def open(self, request: Request, timeout: float):
            raise HTTPError(request.full_url, 404, "missing", {}, None)

    with pytest.raises(NoCoverageError, match="no data"):
        HttpRangeReader(
            "https://data.example.test/missing.tif",
            tmp_path,
            allowed_hosts=frozenset({"data.example.test"}),
            maximum_source_bytes=1_000,
            opener=MissingOpener(),
        )
