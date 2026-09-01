"""Validate compatible local elevation rasters and derive their spatial extent."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from ..errors import RasterFormatError, UserInputError
from ..io.bigtiff_tiles import BigTiffFloatTileReader, open_float_tile_reader
from ..models import ProjectedBounds
from .crs import crs_from_epsg
from .projection import GeographicPoint, ProjectedPoint, project_utm_to_wgs84
from .roi import BBoxWGS84
from .texture_mapping import bounds_fully_covered


@dataclass(frozen=True, slots=True)
class LocalElevationInspection:
    """Validated metadata shared by a local elevation raster set."""

    paths: tuple[Path, ...]
    bounds_wgs84: BBoxWGS84
    epsg_codes: tuple[int, ...]
    projected_bounds: tuple[ProjectedBounds, ...]
    native_resolution_metres: float
    total_source_pixels: int


def resolve_local_elevation_paths(raw_path: str | Path) -> tuple[Path, ...]:
    """Resolve one TIFF or a non-recursive folder of TIFF files."""

    if not str(raw_path).strip():
        raise UserInputError("Choose an elevation TIFF or folder")
    path = Path(raw_path).expanduser().resolve()
    if path.is_file() and path.suffix.lower() in {".tif", ".tiff"}:
        return (path,)
    if path.is_dir():
        paths = tuple(
            candidate.resolve()
            for candidate in sorted(path.iterdir(), key=lambda item: item.name.casefold())
            if candidate.is_file() and candidate.suffix.lower() in {".tif", ".tiff"}
        )
        if paths:
            if len(paths) > 10_000:
                raise UserInputError("Local elevation folder contains too many TIFF files")
            return paths
    raise UserInputError("Choose an elevation TIFF or a folder containing TIFF files")


def inspect_local_elevation(paths: tuple[Path, ...]) -> LocalElevationInspection:
    """Validate a raster set and derive a WGS84 envelope without reading pixel arrays."""

    if not paths:
        raise UserInputError("No local elevation rasters were selected")
    try:
        resolved = tuple(path.expanduser().resolve(strict=True) for path in paths)
    except OSError as exc:
        raise UserInputError("A selected local elevation raster is no longer available") from exc
    if len(set(resolved)) != len(resolved):
        raise UserInputError("Local elevation raster paths must be unique")
    readers = tuple(open_float_tile_reader(path) for path in resolved)
    by_epsg: dict[int, list[tuple[BigTiffFloatTileReader, ProjectedBounds]]] = {}
    total_pixels = 0
    resolutions: set[float] = set()
    geographic_points: list[GeographicPoint] = []
    for reader in readers:
        reference = reader.georeference
        crs = crs_from_epsg(reference.epsg)
        west, south, east, north = reference.bounds(
            reader.layout.width, reader.layout.height
        )
        bounds = ProjectedBounds(west, south, east, north, reference.epsg)
        by_epsg.setdefault(reference.epsg, []).append((reader, bounds))
        resolutions.add(reference.pixel_width)
        total_pixels += reader.layout.width * reader.layout.height
        geographic_points.extend(
            project_utm_to_wgs84(ProjectedPoint(easting, northing, reference.epsg), crs)
            for easting, northing in (
                (west, south),
                (west, north),
                (east, south),
                (east, north),
            )
        )
    if len(resolutions) != 1:
        raise RasterFormatError("Local elevation rasters do not share one pixel resolution")
    projected_bounds = tuple(
        _validate_group(epsg, by_epsg[epsg]) for epsg in sorted(by_epsg)
    )
    bounds_wgs84 = BBoxWGS84(
        min(point.longitude for point in geographic_points),
        min(point.latitude for point in geographic_points),
        max(point.longitude for point in geographic_points),
        max(point.latitude for point in geographic_points),
    )
    return LocalElevationInspection(
        resolved,
        bounds_wgs84,
        tuple(sorted(by_epsg)),
        projected_bounds,
        next(iter(resolutions)),
        total_pixels,
    )


def _validate_group(
    epsg: int,
    entries: list[tuple[BigTiffFloatTileReader, ProjectedBounds]],
) -> ProjectedBounds:
    first = entries[0][0]
    reference = first.georeference
    if reference.pixel_height != -reference.pixel_width:
        raise RasterFormatError("Local elevation pixels must be square and north-up")
    if first.layout.nodata is None:
        raise RasterFormatError("Local elevation rasters must declare a NoData value")
    origin_x = min(reader.georeference.origin_x for reader, _bounds in entries)
    origin_y = max(reader.georeference.origin_y for reader, _bounds in entries)
    for reader, _bounds in entries:
        current = reader.georeference
        if current.epsg != epsg:
            raise AssertionError("Raster group contains an unexpected CRS")
        if (
            current.pixel_width != reference.pixel_width
            or current.pixel_height != reference.pixel_height
        ):
            raise RasterFormatError("Local elevation rasters do not share one pixel grid")
        if reader.layout.nodata != first.layout.nodata:
            raise RasterFormatError("Local elevation rasters do not share one NoData value")
        column = (current.origin_x - origin_x) / reference.pixel_width
        row = (origin_y - current.origin_y) / -reference.pixel_height
        if not math.isclose(column, round(column), abs_tol=1e-8) or not math.isclose(
            row, round(row), abs_tol=1e-8
        ):
            raise RasterFormatError("Local elevation rasters are not aligned to one pixel grid")
    envelope = ProjectedBounds(
        min(bounds.west for _reader, bounds in entries),
        min(bounds.south for _reader, bounds in entries),
        max(bounds.east for _reader, bounds in entries),
        max(bounds.north for _reader, bounds in entries),
        epsg,
    )
    if not bounds_fully_covered(envelope, tuple(bounds for _reader, bounds in entries)):
        raise RasterFormatError("Local elevation rasters contain gaps inside their envelope")
    return envelope
