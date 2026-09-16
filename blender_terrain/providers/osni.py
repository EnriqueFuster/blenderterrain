"""Parse OSNI DTM sample coordinates without assuming a horizontal CRS."""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from zipfile import ZipFile

from ..errors import ProviderUnavailableError, RasterFormatError
from ..io.random_access import HttpRangeReader, RandomAccessIO

OSNI_CRS_EPSG = 29903
_ADMIN_HOST = "admin.opendatani.gov.uk"
_STORAGE_HOST = "83025b28472d6aa2bf5ae59f3724aa78.eu.r2.cloudflarestorage.com"


def iter_osni_xyz(lines: Iterable[str]) -> Iterator[tuple[float, float, float]]:
    """Yield finite x/y/height rows after the observed three-column header."""

    iterator = enumerate(lines, start=1)
    for _line_number, line in iterator:
        if line.strip():
            if line.split() != ["x", "y", "z"]:
                raise RasterFormatError("OSNI sample must start with an x y z header")
            break
    else:
        raise RasterFormatError("OSNI sample contains no x y z header")

    for line_number, line in iterator:
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 3:
            raise RasterFormatError(f"OSNI sample line {line_number} has no x y z triple")
        try:
            point = (float(fields[0]), float(fields[1]), float(fields[2]))
        except ValueError as exc:
            raise RasterFormatError(f"OSNI sample line {line_number} is not numeric") from exc
        if not all(math.isfinite(value) for value in point):
            raise RasterFormatError(f"OSNI sample line {line_number} is not finite")
        yield point


def open_osni_archive(
    resource_url: str,
    cache_directory: Path,
    *,
    opener: Any | None = None,
) -> ZipFile:
    """Open an official grouped DTM ZIP without downloading every sheet."""

    resolved = _resolve_resource_url(resource_url, opener)
    source = HttpRangeReader(
        resolved,
        cache_directory,
        allowed_hosts=frozenset({_STORAGE_HOST}),
        maximum_source_bytes=250_000_000,
    )
    return ZipFile(RandomAccessIO(source))


def _resolve_resource_url(resource_url: str, opener: Any | None = None) -> str:
    parsed = urlsplit(resource_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != _ADMIN_HOST
        or "/resource/" not in parsed.path
        or "/download/" not in parsed.path
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Expected an official Open Data NI resource URL")
    request = Request(
        resource_url,
        headers={
            "User-Agent": "BlenderTerrain/0.5",
            "Referer": "https://www.opendatani.gov.uk/",
            "Range": "bytes=0-0",
        },
    )
    try:
        with (opener or build_opener()).open(request, timeout=30.0) as response:
            resolved = str(response.geturl())
            response.read(2)
    except OSError as exc:
        raise ProviderUnavailableError("Open Data NI resource redirect failed") from exc
    target = urlsplit(resolved)
    if (
        target.scheme != "https"
        or target.hostname != _STORAGE_HOST
        or target.username
        or target.password
        or not target.query
    ):
        raise ProviderUnavailableError("Open Data NI returned an untrusted resource redirect")
    return resolved
