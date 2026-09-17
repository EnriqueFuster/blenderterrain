"""Acquire OSNI 10 m DTM sheets in their published Irish Grid CRS."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from io import TextIOWrapper
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from zipfile import ZipFile

import numpy as np
from numpy.typing import NDArray

from ..catalog import (
    Catalog,
    DatasetKind,
    LayerRequest,
    ProductSelection,
    load_bundled_catalog,
)
from ..core.acquisition import AcquiredRasterLayer
from ..core.delivery import TransferProgress
from ..core.roi import BBoxWGS84
from ..errors import JobCancelled, NoCoverageError, ProviderUnavailableError, RasterFormatError
from ..io.elevation_window import elevation_window_is_valid, write_elevation_window
from ..io.random_access import HttpRangeReader, RandomAccessIO
from ..models import ProjectedBounds

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
_NODATA = -9999.0
_MAXIMUM_GRID_BYTES = 1_000_000
_MAXIMUM_SHEET_BYTES = 50_000_000


class ArchiveOpener(Protocol):
    def __call__(self, resource_url: str, cache_directory: Path) -> ZipFile: ...


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


def load_osni_grid(cache_directory: Path) -> tuple[OsniGridCell, ...]:
    """Load and cache the small official coverage index through bounded range reads."""

    resolved = _resolve_resource_url(OSNI_GRID_URL)
    source = HttpRangeReader(
        resolved,
        cache_directory,
        allowed_hosts=frozenset({_STORAGE_HOST}),
        maximum_source_bytes=_MAXIMUM_GRID_BYTES,
        cache_key=OSNI_GRID_URL,
    )
    try:
        document = json.loads(source.read(0, source.size))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RasterFormatError("OSNI coverage grid is not valid GeoJSON") from exc
    if not isinstance(document, dict):
        raise RasterFormatError("OSNI coverage grid is not a GeoJSON object")
    return parse_osni_grid(document)


def read_osni_sheet(archive: ZipFile, sheet: int) -> tuple[NDArray[np.float32], ProjectedBounds]:
    """Decode one regular 10 m XYZ sheet using bounded memory."""

    member = osni_member_name(sheet)
    try:
        info = archive.getinfo(member)
    except KeyError as exc:
        raise RasterFormatError(f"OSNI archive does not contain {member}") from exc
    if info.file_size <= 0 or info.file_size > _MAXIMUM_SHEET_BYTES:
        raise RasterFormatError(f"OSNI sheet {member} has an invalid size")

    minimum_x = minimum_y = math.inf
    maximum_x = maximum_y = -math.inf
    count = 0
    for x, y, _height in _iter_archive_points(archive, member):
        minimum_x, maximum_x = min(minimum_x, x), max(maximum_x, x)
        minimum_y, maximum_y = min(minimum_y, y), max(maximum_y, y)
        count += 1
    columns = round((maximum_x - minimum_x) / 10.0) + 1
    rows = round((maximum_y - minimum_y) / 10.0) + 1
    if columns < 2 or rows < 2 or count > rows * columns:
        raise RasterFormatError(f"OSNI sheet {member} is not a valid 10 m grid")

    data = np.full((rows, columns), _NODATA, dtype=np.float32)
    for x, y, height in _iter_archive_points(archive, member):
        column = round((x - minimum_x) / 10.0)
        row = round((maximum_y - y) / 10.0)
        if (
            not 0 <= row < rows
            or not 0 <= column < columns
            or data[row, column] != _NODATA
            or abs(x - (minimum_x + column * 10.0)) > 0.01
            or abs(y - (maximum_y - row * 10.0)) > 0.01
        ):
            raise RasterFormatError(f"OSNI sheet {member} has an irregular point grid")
        data[row, column] = height
    return data, ProjectedBounds(
        minimum_x - 5.0,
        minimum_y - 5.0,
        maximum_x + 5.0,
        maximum_y + 5.0,
        OSNI_CRS_EPSG,
    )


def _iter_archive_points(archive: ZipFile, member: str) -> Iterator[tuple[float, float, float]]:
    with archive.open(member) as binary:
        yield from iter_osni_xyz(TextIOWrapper(binary, encoding="utf-8-sig"))


class OsniAcquirer:
    """Extract only selected OSNI sheets and publish native Irish Grid windows."""

    def __init__(
        self,
        catalog: Catalog | None = None,
        cells: tuple[OsniGridCell, ...] | None = None,
        archive_opener: ArchiveOpener | None = None,
    ) -> None:
        self.catalog = catalog or load_bundled_catalog()
        self.cells = cells
        self.archive_opener = archive_opener or open_osni_archive

    def acquire(
        self,
        selection: ProductSelection,
        request: LayerRequest,
        roi: BBoxWGS84,
        cache_directory: Path,
        progress_callback: Callable[[TransferProgress], None] | None = None,
        cancellation_requested: Callable[[], bool] = lambda: False,
    ) -> AcquiredRasterLayer:
        product = self.catalog.product(selection.product_id)
        if (
            selection.provider_id != "osni"
            or product.provider_id != selection.provider_id
            or selection.kind is not DatasetKind.DTM
            or selection.kind is not request.kind
            or product.capabilities.kind is not selection.kind
        ):
            raise ValueError("OSNI received an incompatible selection")
        cells = self.cells or load_osni_grid(cache_directory / "coverage")
        sheets = select_osni_sheets(cells, roi.west, roi.south, roi.east, roi.north)
        if not sheets:
            raise NoCoverageError("OSNI DTM does not intersect the requested ROI")

        target = cache_directory / "osni" / product.id / "native-v1"
        paths: list[Path] = []
        cached_count = 0
        pending: dict[tuple[int, int], list[tuple[int, Path]]] = {}
        for sheet in sheets:
            path = target / f"sheet_{sheet:03d}.npy"
            paths.append(path)
            if elevation_window_is_valid(path):
                cached_count += 1
            else:
                pending.setdefault(osni_archive_range(sheet), []).append((sheet, path))

        completed = cached_count
        for archive_range, members in pending.items():
            if cancellation_requested():
                raise JobCancelled("OSNI acquisition was cancelled")
            with self.archive_opener(
                OSNI_ARCHIVE_URLS[archive_range], target / "ranges"
            ) as archive:
                for sheet, path in members:
                    if cancellation_requested():
                        raise JobCancelled("OSNI acquisition was cancelled")
                    data, bounds = read_osni_sheet(archive, sheet)
                    path.unlink(missing_ok=True)
                    path.with_suffix(".npy.json").unlink(missing_ok=True)
                    write_elevation_window(path, data, bounds, _NODATA)
                    completed += 1
                    if progress_callback is not None:
                        size = path.stat().st_size
                        progress_callback(
                            TransferProgress(
                                selection.kind.value,
                                completed - 1,
                                len(paths),
                                path.name,
                                size,
                                size,
                            )
                        )
        return AcquiredRasterLayer(
            selection.provider_id,
            product.id,
            selection.kind,
            tuple(paths),
            cached_count,
        )


def iter_osni_xyz(lines: Iterable[str]) -> Iterator[tuple[float, float, float]]:
    """Yield finite x/y/height rows with or without the sample-file header."""

    iterator = enumerate(lines, start=1)
    for line_number, line in iterator:
        if line.strip():
            if [field.lower() for field in line.split()] != ["x", "y", "z"]:
                yield _parse_osni_xyz_line(line, line_number)
            break
    else:
        raise RasterFormatError("OSNI sheet contains no XYZ points")

    for line_number, line in iterator:
        if not line.strip():
            continue
        yield _parse_osni_xyz_line(line, line_number)


def _parse_osni_xyz_line(line: str, line_number: int) -> tuple[float, float, float]:
    fields = line.split()
    if len(fields) != 3:
        raise RasterFormatError(f"OSNI sample line {line_number} has no x y z triple")
    try:
        point = (float(fields[0]), float(fields[1]), float(fields[2]))
    except ValueError as exc:
        raise RasterFormatError(f"OSNI sample line {line_number} is not numeric") from exc
    if not all(math.isfinite(value) for value in point):
        raise RasterFormatError(f"OSNI sample line {line_number} is not finite")
    return point


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
        cache_key=resource_url,
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
