"""Parse OSNI DTM sample coordinates without assuming a horizontal CRS."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
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
_GRID_NAME = re.compile(r"^(?P<sheet>\d{1,3})(?:ne|nw|se|sw)\.tif$", re.IGNORECASE)
_ARCHIVE_RANGES = ((1, 50), (51, 100), (101, 150), (151, 200), (201, 250), (251, 293))
OSNI_GRID_URL = (
    "https://admin.opendatani.gov.uk/dataset/18a0c1f3-0e2a-406f-9d5f-ce190a166895/"
    "resource/3400824a-ae20-483d-b633-a936d8f45f8b/download/"
    "osni_open_data_coverage_grid_10k.geojson"
)
OSNI_ARCHIVE_URLS = {
    (1, 50): (
        "https://admin.opendatani.gov.uk/dataset/bea57cd0-c9aa-45cd-b048-3b6d60b04fbe/"
        "resource/2974c9cb-0027-4ee4-9095-02c50b6b73a7/download/"
        "osni_10m_dtm_sheets_1-50.zip"
    ),
    (51, 100): (
        "https://admin.opendatani.gov.uk/dataset/260f5296-2469-4eea-959d-dbbca9c29c0f/"
        "resource/42604b59-22ca-45d0-9dfe-8b6e961e1961/download/"
        "osni_10m_dtm_sheets_51-100.zip"
    ),
    (101, 150): (
        "https://admin.opendatani.gov.uk/dataset/64ccceb5-c06c-484d-8b9c-699357fc6b13/"
        "resource/a48821ab-8388-48ef-a9aa-97b8158cbc9b/download/"
        "osni_10m_dtm_sheets_101-150.zip"
    ),
    (151, 200): (
        "https://admin.opendatani.gov.uk/dataset/adea7269-2a86-4953-94bd-7b4434baba26/"
        "resource/a6392f75-d45a-4d2b-a8e8-678d86419f7a/download/"
        "osni_10m_dtm_sheets_151-200.zip"
    ),
    (201, 250): (
        "https://admin.opendatani.gov.uk/dataset/e0e15a4a-32a4-4f43-8bb0-1233262fee06/"
        "resource/beddd617-b078-495f-a856-2f1b8d550d59/download/"
        "osni_10m_dtm_sheets_201-250.zip"
    ),
    (251, 293): (
        "https://admin.opendatani.gov.uk/dataset/93fb6a92-f6eb-4f38-b6db-9ba5b880c02b/"
        "resource/653b468d-f582-4bca-88a7-66a5a4f2b289/download/"
        "osni_10m_dtm_sheets_251-293.zip"
    ),
}


@dataclass(frozen=True, slots=True)
class OsniGridCell:
    """One published OSNI quarter-sheet coverage cell in CRS84."""

    sheet: int
    west: float
    south: float
    east: float
    north: float

    def intersects(self, west: float, south: float, east: float, north: float) -> bool:
        return self.west < east and self.east > west and self.south < north and self.north > south


def parse_osni_grid(document: Mapping[str, Any]) -> tuple[OsniGridCell, ...]:
    """Parse usable cells from the official CRS84 coverage GeoJSON."""

    crs = document.get("crs", {}).get("properties", {}).get("name")
    if crs != "urn:ogc:def:crs:OGC:1.3:CRS84":
        raise RasterFormatError("OSNI coverage grid must use OGC CRS84")
    cells: list[OsniGridCell] = []
    for feature in document.get("features", ()):
        name = str(feature.get("properties", {}).get("NAME", "")).strip()
        match = _GRID_NAME.fullmatch(name)
        if match is None:
            continue
        geometry = feature.get("geometry", {})
        if geometry.get("type") != "Polygon":
            raise RasterFormatError(f"OSNI coverage cell {name} is not a polygon")
        try:
            ring = geometry["coordinates"][0]
            longitudes = [float(point[0]) for point in ring]
            latitudes = [float(point[1]) for point in ring]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise RasterFormatError(f"OSNI coverage cell {name} has invalid coordinates") from exc
        if not longitudes or not all(math.isfinite(value) for value in (*longitudes, *latitudes)):
            raise RasterFormatError(f"OSNI coverage cell {name} has invalid coordinates")
        cells.append(
            OsniGridCell(
                int(match.group("sheet")),
                min(longitudes),
                min(latitudes),
                max(longitudes),
                max(latitudes),
            )
        )
    if not cells:
        raise RasterFormatError("OSNI coverage grid contains no named cells")
    return tuple(cells)


def select_osni_sheets(
    cells: Iterable[OsniGridCell], west: float, south: float, east: float, north: float
) -> tuple[int, ...]:
    """Return unique sheet numbers intersecting a WGS84 bounding box."""

    if west >= east or south >= north:
        raise ValueError("ROI bounds must have positive width and height")
    return tuple(
        sorted({cell.sheet for cell in cells if cell.intersects(west, south, east, north)})
    )


def osni_archive_range(sheet: int) -> tuple[int, int]:
    """Return the official grouped archive range containing a sheet."""

    for first, last in _ARCHIVE_RANGES:
        if first <= sheet <= last:
            return first, last
    raise ValueError("OSNI sheet number must be between 1 and 293")


def osni_member_name(sheet: int) -> str:
    """Return the observed TXT member name for a published sheet."""

    osni_archive_range(sheet)
    return f"Sheet{sheet:03d}v4.txt"


def osni_archive_url(sheet: int) -> str:
    """Return the verified official resource URL containing a sheet."""

    return OSNI_ARCHIVE_URLS[osni_archive_range(sheet)]


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
