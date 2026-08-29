"""Compose aligned elevation windows while preserving overlap diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from ..errors import RasterFormatError
from ..models import ProjectedBounds
from .bigtiff_tiles import GeoReference, TileLayout


class ElevationReader(Protocol):
    layout: TileLayout
    georeference: GeoReference

    @property
    def nodata(self) -> float: ...

    def read_bounds(
        self, bounds: ProjectedBounds
    ) -> tuple[NDArray[np.float32], ProjectedBounds]: ...


@dataclass(frozen=True, slots=True)
class ElevationMosaic:
    """An aligned elevation array with per-pixel source and overlap evidence."""

    data: NDArray[np.float32]
    source_index: NDArray[np.int16]
    bounds: ProjectedBounds
    nodata: float
    overlap_valid_pixels: int
    conflicting_valid_pixels: int
    maximum_overlap_difference: float


def read_elevation_mosaic(
    readers: tuple[ElevationReader, ...],
    requested_bounds: ProjectedBounds,
    maximum_pixels: int = 16_777_216,
) -> ElevationMosaic:
    """Read a bounded mosaic; earlier sources win conflicting valid overlaps."""

    if not readers:
        raise ValueError("At least one elevation source is required")
    if maximum_pixels <= 0:
        raise ValueError("Maximum mosaic pixels must be positive")
    grid = _common_grid(readers, requested_bounds.epsg)
    target_window = grid.enclosing_window(requested_bounds)
    if target_window.width * target_window.height > maximum_pixels:
        raise ValueError("Requested elevation mosaic exceeds the configured pixel limit")
    target_bounds = grid.window_bounds(target_window)
    nodata = readers[0].nodata

    data = np.full((target_window.height, target_window.width), nodata, dtype=np.float32)
    source_index = np.full(data.shape, -1, dtype=np.int16)
    covered = np.zeros(data.shape, dtype=bool)
    overlap_valid_pixels = 0
    conflicting_valid_pixels = 0
    maximum_overlap_difference = 0.0

    for index, reader in enumerate(readers):
        intersection = _intersection(target_bounds, _reader_bounds(reader))
        if intersection is None:
            continue
        source_data, source_bounds = reader.read_bounds(intersection)
        destination = grid.enclosing_window(source_bounds)
        destination_row = destination.row - target_window.row
        destination_column = destination.column - target_window.column
        rows = slice(destination_row, destination_row + destination.height)
        columns = slice(destination_column, destination_column + destination.width)
        current = data[rows, columns]
        current_sources = source_index[rows, columns]
        current_covered = covered[rows, columns]
        valid_new = (
            np.ones(source_data.shape, dtype=bool)
            if reader.layout.nodata is None
            else source_data != reader.layout.nodata
        )
        valid_current = current_sources >= 0
        overlap = valid_current & valid_new
        if overlap.any():
            differences = np.abs(current[overlap] - source_data[overlap])
            overlap_valid_pixels += int(overlap.sum())
            conflicting_valid_pixels += int(np.count_nonzero(differences))
            maximum_overlap_difference = max(
                maximum_overlap_difference, float(differences.max())
            )
        fill = valid_new & ~valid_current
        current[fill] = source_data[fill]
        current_sources[fill] = index
        current_covered[:] = True

    if not covered.all():
        raise RasterFormatError("Elevation sources do not cover the complete requested area")
    return ElevationMosaic(
        data=data,
        source_index=source_index,
        bounds=target_bounds,
        nodata=nodata,
        overlap_valid_pixels=overlap_valid_pixels,
        conflicting_valid_pixels=conflicting_valid_pixels,
        maximum_overlap_difference=maximum_overlap_difference,
    )


def _common_grid(
    readers: tuple[ElevationReader, ...], expected_epsg: int
) -> GeoReference:
    first = readers[0]
    pixel_width = first.georeference.pixel_width
    pixel_height = first.georeference.pixel_height
    nodata = first.layout.nodata
    origin_x = min(reader.georeference.origin_x for reader in readers)
    origin_y = max(reader.georeference.origin_y for reader in readers)
    for reader in readers:
        reference = reader.georeference
        if reference.epsg != expected_epsg:
            raise RasterFormatError("Elevation sources do not share the requested CRS")
        if reference.pixel_width != pixel_width or reference.pixel_height != pixel_height:
            raise RasterFormatError("Elevation sources do not share a pixel resolution")
        if reader.layout.nodata != nodata:
            raise RasterFormatError("Elevation sources do not share a NoData value")
        column_offset = (reference.origin_x - origin_x) / pixel_width
        row_offset = (origin_y - reference.origin_y) / -pixel_height
        if not _is_grid_integer(column_offset) or not _is_grid_integer(row_offset):
            raise RasterFormatError("Elevation sources are not aligned to one pixel grid")
    return GeoReference(
        epsg=expected_epsg,
        origin_x=origin_x,
        origin_y=origin_y,
        pixel_width=pixel_width,
        pixel_height=pixel_height,
        declared_epsg=expected_epsg,
    )


def _reader_bounds(reader: ElevationReader) -> ProjectedBounds:
    west, south, east, north = reader.georeference.bounds(
        reader.layout.width, reader.layout.height
    )
    return ProjectedBounds(west, south, east, north, reader.georeference.epsg)


def _intersection(left: ProjectedBounds, right: ProjectedBounds) -> ProjectedBounds | None:
    west = max(left.west, right.west)
    south = max(left.south, right.south)
    east = min(left.east, right.east)
    north = min(left.north, right.north)
    if east <= west or north <= south:
        return None
    return ProjectedBounds(west, south, east, north, left.epsg)


def _is_grid_integer(value: float) -> bool:
    return abs(value - round(value)) <= 1e-9
