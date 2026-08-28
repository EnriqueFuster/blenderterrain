from __future__ import annotations

from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.error import URLError

import pytest

from blender_terrain.errors import DownloadIntegrityError, JobCancelled
from blender_terrain.io.http_download import download_public_tiff

TIFF = b"II*\x00\x08\x00\x00\x00\x00"


class Response(BytesIO):
    def __init__(self, payload: bytes, content_type: str = "image/tiff") -> None:
        super().__init__(payload)
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(payload))

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class SequenceOpener:
    def __init__(self, outcomes: list[Response | Exception]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def open(self, request: object, timeout: float) -> Response:
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_downloads_atomically_and_reuses_valid_cache(tmp_path: Path) -> None:
    opener = SequenceOpener([Response(TIFF)])
    progress: list[tuple[int, int | None]] = []
    (tmp_path / "source.tif.part").write_bytes(b"interrupted download")

    downloaded = download_public_tiff(
        "https://example.test/source.tif",
        tmp_path,
        "source.tif",
        maximum_bytes=100,
        opener=opener,
        progress_callback=lambda written, total: progress.append((written, total)),
    )
    cached = download_public_tiff(
        "https://example.test/source.tif",
        tmp_path,
        "source.tif",
        maximum_bytes=100,
        opener=SequenceOpener([]),
    )

    assert downloaded.path.read_bytes() == TIFF
    assert not downloaded.cached
    assert cached.cached
    assert progress == [(len(TIFF), len(TIFF))]
    assert not (tmp_path / "source.tif.part").exists()


def test_cached_file_still_obeys_size_limit(tmp_path: Path) -> None:
    (tmp_path / "source.tif").write_bytes(TIFF)

    with pytest.raises(DownloadIntegrityError, match="Cached raster exceeds"):
        download_public_tiff(
            "https://example.test/source.tif",
            tmp_path,
            "source.tif",
            maximum_bytes=8,
        )


def test_retries_a_transient_network_failure(tmp_path: Path) -> None:
    opener = SequenceOpener([URLError("offline"), Response(TIFF)])
    delays: list[float] = []

    result = download_public_tiff(
        "https://example.test/source.tif",
        tmp_path,
        "source.tif",
        maximum_bytes=100,
        retries=1,
        opener=opener,
        sleeper=delays.append,
    )

    assert result.path.is_file()
    assert opener.calls == 2
    assert delays == [0.25]


def test_rejects_declared_or_actual_oversize(tmp_path: Path) -> None:
    with pytest.raises(DownloadIntegrityError, match="Declared"):
        download_public_tiff(
            "https://example.test/source.tif",
            tmp_path,
            "source.tif",
            maximum_bytes=8,
            opener=SequenceOpener([Response(TIFF)]),
        )

    response = Response(TIFF)
    del response.headers["Content-Length"]
    with pytest.raises(DownloadIntegrityError, match="exceeds"):
        download_public_tiff(
            "https://example.test/source.tif",
            tmp_path,
            "source.tif",
            maximum_bytes=8,
            opener=SequenceOpener([response]),
        )
    assert not (tmp_path / "source.tif.part").exists()


def test_cancellation_removes_partial_file(tmp_path: Path) -> None:
    with pytest.raises(JobCancelled):
        download_public_tiff(
            "https://example.test/source.tif",
            tmp_path,
            "source.tif",
            maximum_bytes=100,
            opener=SequenceOpener([Response(TIFF)]),
            cancelled=lambda: True,
        )

    assert not (tmp_path / "source.tif.part").exists()


def test_rejects_non_tiff_response_and_unsafe_url(tmp_path: Path) -> None:
    with pytest.raises(DownloadIntegrityError, match="content type"):
        download_public_tiff(
            "https://example.test/source.tif",
            tmp_path,
            "source.tif",
            maximum_bytes=100,
            opener=SequenceOpener([Response(b"<html>", "text/html")]),
        )
    with pytest.raises(ValueError, match="HTTPS"):
        download_public_tiff(
            "http://example.test/source.tif",
            tmp_path,
            "source.tif",
            maximum_bytes=100,
        )
