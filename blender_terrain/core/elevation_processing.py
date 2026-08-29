"""Windowed elevation mosaicking and bilinear resampling for terrain tiles."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from ..errors import RasterFormatError
from ..io.bigtiff_tiles import open_float_tile_reader
from ..io.elevation_mosaic import ElevationReader, read_elevation_mosaic
from ..io.elevation_window import ElevationWindowReader
from ..models import ProjectedBounds
from .crs import CRSInfo
from .grid import GridTile
from .planning import ImportPlan
from .projection import project_utm_arrays_to_wgs84, project_wgs84_to_utm
from .roi import PolygonWGS84, RegionOfInterest

DEFAULT_MAX_SOURCE_WINDOW_PIXELS = 4_194_304


@dataclass(frozen=True, slots=True)
class ProcessedElevationTile:
    """One final elevation grid ready for later Blender mesh construction."""

    zone_index: int
    tile: GridTile
    data: NDArray[np.float32]
    nodata: float
    overlap_valid_pixels: int
    conflicting_valid_pixels: int
    maximum_overlap_difference: float


def process_elevation_tiles(
    source_paths: tuple[Path, ...],
    plan: ImportPlan,
    progress_callback: Callable[[int, int], None] | None = None,
    maximum_source_window_pixels: int = DEFAULT_MAX_SOURCE_WINDOW_PIXELS,
    region: RegionOfInterest | None = None,
) -> tuple[ProcessedElevationTile, ...]:
    """Build every planned terrain tile while bounding native source windows."""

    if maximum_source_window_pixels <= 0:
        raise ValueError("Maximum source window pixels must be positive")
    readers = tuple(
        ElevationWindowReader(path)
        if path.suffix.lower() == ".npy"
        else open_float_tile_reader(path)
        for path in source_paths
    )
    outputs: list[ProcessedElevationTile] = []
    total_tiles = plan.terrain_tile_count
    if progress_callback is not None:
        progress_callback(0, total_tiles)
    for zone_index, grid in enumerate(plan.grids):
        zone_readers = tuple(
            reader for reader in readers if reader.georeference.epsg == grid.bounds.epsg
        )
        geographic_readers = tuple(
            reader for reader in readers if reader.georeference.epsg == 4326
        )
        if not zone_readers and not geographic_readers:
            raise RasterFormatError(f"No elevation source is available for EPSG:{grid.bounds.epsg}")
        for tile in plan.tiles_for_grid(zone_index):
            processed = (
                _resample_tile(
                    zone_index, tile, zone_readers, maximum_source_window_pixels
                )
                if zone_readers
                else _resample_geographic_tile(
                    zone_index,
                    tile,
                    geographic_readers,
                    plan.work_areas[zone_index].crs,
                    maximum_source_window_pixels,
                )
            )
            if region is not None:
                crs = plan.work_areas[zone_index].crs
                _mask_outside_region(processed.data, tile, processed.nodata, region, crs)
            outputs.append(processed)
            if progress_callback is not None:
                progress_callback(len(outputs), total_tiles)
    return tuple(outputs)


def _resample_tile(
    zone_index: int,
    tile: GridTile,
    readers: tuple[ElevationReader, ...],
    maximum_source_window_pixels: int,
) -> ProcessedElevationTile:
    source_resolution = readers[0].georeference.pixel_width
    if source_resolution <= 0.0:
        raise RasterFormatError("Elevation source pixel width must be positive")
    target_resolution = (tile.bounds.east - tile.bounds.west) / tile.columns
    maximum_block_cells = max(
        1,
        math.floor(
            math.sqrt(maximum_source_window_pixels) * source_resolution / target_resolution
        ),
    )
    nodata = readers[0].nodata
    output = np.full((tile.rows + 1, tile.columns + 1), nodata, dtype=np.float32)
    overlap = 0
    conflicts = 0
    maximum_difference = 0.0

    for row in range(0, tile.rows, maximum_block_cells):
        block_rows = min(maximum_block_cells, tile.rows - row)
        north = tile.bounds.north - row * target_resolution
        south = north - block_rows * target_resolution
        for column in range(0, tile.columns, maximum_block_cells):
            block_columns = min(maximum_block_cells, tile.columns - column)
            west = tile.bounds.west + column * target_resolution
            east = west + block_columns * target_resolution
            requested = ProjectedBounds(west, south, east, north, tile.bounds.epsg)
            mosaic = read_elevation_mosaic(
                readers, requested, maximum_pixels=maximum_source_window_pixels
            )
            output[row : row + block_rows + 1, column : column + block_columns + 1] = (
                _bilinear_sample_grid(
                    mosaic.data,
                    mosaic.bounds.west,
                    mosaic.bounds.north,
                    source_resolution,
                    requested,
                    block_rows + 1,
                    block_columns + 1,
                    mosaic.nodata,
                )
            )
            overlap += mosaic.overlap_valid_pixels
            conflicts += mosaic.conflicting_valid_pixels
            maximum_difference = max(maximum_difference, mosaic.maximum_overlap_difference)
    return ProcessedElevationTile(
        zone_index,
        tile,
        output,
        nodata,
        overlap,
        conflicts,
        maximum_difference,
    )


def _resample_geographic_tile(
    zone_index: int,
    tile: GridTile,
    readers: tuple[ElevationReader, ...],
    crs: CRSInfo,
    maximum_source_window_pixels: int,
) -> ProcessedElevationTile:
    """Window and reproject geographic elevation into one projected terrain tile."""

    source_resolution = readers[0].georeference.pixel_width
    if source_resolution <= 0.0:
        raise RasterFormatError("Elevation source pixel width must be positive")
    target_resolution = (tile.bounds.east - tile.bounds.west) / tile.columns
    # Use the conservative north-south degree length to keep source windows bounded.
    source_resolution_metres = source_resolution * 110_000.0
    maximum_block_cells = min(
        512,
        max(
            1,
            math.floor(
                math.sqrt(maximum_source_window_pixels)
                * source_resolution_metres
                / target_resolution
            ),
        ),
    )
    nodata = readers[0].nodata
    output = np.full((tile.rows + 1, tile.columns + 1), nodata, dtype=np.float32)
    overlap = 0
    conflicts = 0
    maximum_difference = 0.0

    for row in range(0, tile.rows, maximum_block_cells):
        block_rows = min(maximum_block_cells, tile.rows - row)
        north = tile.bounds.north - row * target_resolution
        south = north - block_rows * target_resolution
        target_y = np.linspace(north, south, block_rows + 1, dtype=np.float64)
        for column in range(0, tile.columns, maximum_block_cells):
            block_columns = min(maximum_block_cells, tile.columns - column)
            west = tile.bounds.west + column * target_resolution
            east = west + block_columns * target_resolution
            target_x = np.linspace(west, east, block_columns + 1, dtype=np.float64)
            eastings, northings = np.meshgrid(target_x, target_y)
            longitude, latitude = project_utm_arrays_to_wgs84(eastings, northings, crs)
            requested = ProjectedBounds(
                float(longitude.min()),
                float(latitude.min()),
                float(longitude.max()),
                float(latitude.max()),
                4326,
            )
            mosaic = read_elevation_mosaic(
                readers, requested, maximum_pixels=maximum_source_window_pixels
            )
            output[row : row + block_rows + 1, column : column + block_columns + 1] = (
                _bilinear_sample_coordinates(
                    mosaic.data,
                    mosaic.bounds.west,
                    mosaic.bounds.north,
                    source_resolution,
                    longitude,
                    latitude,
                    mosaic.nodata,
                )
            )
            overlap += mosaic.overlap_valid_pixels
            conflicts += mosaic.conflicting_valid_pixels
            maximum_difference = max(maximum_difference, mosaic.maximum_overlap_difference)
    return ProcessedElevationTile(
        zone_index,
        tile,
        output,
        nodata,
        overlap,
        conflicts,
        maximum_difference,
    )


def _mask_outside_region(
    data: NDArray[np.float32],
    tile: GridTile,
    nodata: float,
    region: RegionOfInterest,
    crs: CRSInfo,
) -> None:
    """Set elevation nodes outside a projected polygon ROI to NoData in bounded rows."""

    projected = tuple(_project_polygon(polygon, crs) for polygon in region.polygons)
    x = np.linspace(tile.bounds.west, tile.bounds.east, tile.columns + 1)
    y = np.linspace(tile.bounds.north, tile.bounds.south, tile.rows + 1)
    for row_start in range(0, len(y), 512):
        row_end = min(len(y), row_start + 512)
        xx, yy = np.meshgrid(x, y[row_start:row_end])
        inside = np.zeros(xx.shape, dtype=np.bool_)
        for exterior, holes in projected:
            polygon_inside = _points_in_ring(xx, yy, exterior)
            for hole in holes:
                polygon_inside &= ~_points_in_ring(xx, yy, hole)
            inside |= polygon_inside
        block = data[row_start:row_end]
        block[~inside] = nodata


def _project_polygon(
    polygon: PolygonWGS84, crs: CRSInfo
) -> tuple[NDArray[np.float64], tuple[NDArray[np.float64], ...]]:
    def project_ring(ring: tuple[tuple[float, float], ...]) -> NDArray[np.float64]:
        coordinates = []
        for longitude, latitude in ring:
            point = project_wgs84_to_utm(longitude, latitude, crs)
            coordinates.append((point.easting, point.northing))
        return np.asarray(coordinates, dtype=np.float64)

    return project_ring(polygon.exterior), tuple(project_ring(hole) for hole in polygon.holes)


def _points_in_ring(
    x: NDArray[np.float64], y: NDArray[np.float64], ring: NDArray[np.float64]
) -> NDArray[np.bool_]:
    inside = np.zeros(x.shape, dtype=np.bool_)
    boundary = np.zeros(x.shape, dtype=np.bool_)
    for index in range(len(ring) - 1):
        left_x, left_y = ring[index]
        right_x, right_y = ring[index + 1]
        edge_x = right_x - left_x
        edge_y = right_y - left_y
        cross_product = edge_x * (y - left_y) - edge_y * (x - left_x)
        edge_length = np.hypot(edge_x, edge_y)
        on_line = np.abs(cross_product) <= 1e-7 * max(1.0, edge_length)
        within_edge = (
            (x >= min(left_x, right_x) - 1e-7)
            & (x <= max(left_x, right_x) + 1e-7)
            & (y >= min(left_y, right_y) - 1e-7)
            & (y <= max(left_y, right_y) + 1e-7)
        )
        boundary |= on_line & within_edge
        crosses = (left_y > y) != (right_y > y)
        crossing_x = (right_x - left_x) * (y - left_y) / (
            right_y - left_y + np.finfo(np.float64).eps
        ) + left_x
        inside ^= crosses & (x < crossing_x)
    return inside | boundary


def _bilinear_resample(
    source: NDArray[np.float32],
    source_west: float,
    source_north: float,
    source_resolution: float,
    target_bounds: ProjectedBounds,
    target_rows: int,
    target_columns: int,
    nodata: float,
) -> NDArray[np.float32]:
    west = target_bounds.west
    east = target_bounds.east
    north = target_bounds.north
    south = target_bounds.south
    target_x = west + (np.arange(target_columns, dtype=np.float64) + 0.5) * (
        (east - west) / target_columns
    )
    target_y = north - (np.arange(target_rows, dtype=np.float64) + 0.5) * (
        (north - south) / target_rows
    )
    return _bilinear_sample(
        source, source_west, source_north, source_resolution, target_x, target_y, nodata
    )


def _bilinear_sample_grid(
    source: NDArray[np.float32],
    source_west: float,
    source_north: float,
    source_resolution: float,
    target_bounds: ProjectedBounds,
    target_rows: int,
    target_columns: int,
    nodata: float,
) -> NDArray[np.float32]:
    target_x = np.linspace(
        target_bounds.west, target_bounds.east, target_columns, dtype=np.float64
    )
    target_y = np.linspace(
        target_bounds.north, target_bounds.south, target_rows, dtype=np.float64
    )
    return _bilinear_sample(
        source, source_west, source_north, source_resolution, target_x, target_y, nodata
    )


def _bilinear_sample(
    source: NDArray[np.float32],
    source_west: float,
    source_north: float,
    source_resolution: float,
    target_x: NDArray[np.float64],
    target_y: NDArray[np.float64],
    nodata: float,
) -> NDArray[np.float32]:
    x_coordinates, y_coordinates = np.meshgrid(target_x, target_y)
    return _bilinear_sample_coordinates(
        source,
        source_west,
        source_north,
        source_resolution,
        x_coordinates,
        y_coordinates,
        nodata,
    )


def _bilinear_sample_coordinates(
    source: NDArray[np.float32],
    source_west: float,
    source_north: float,
    source_resolution: float,
    target_x: NDArray[np.float64],
    target_y: NDArray[np.float64],
    nodata: float,
) -> NDArray[np.float32]:
    """Bilinearly sample arbitrary two-dimensional coordinates."""

    if target_x.shape != target_y.shape:
        raise ValueError("Target coordinate arrays must have the same shape")
    source_x = (target_x - source_west) / source_resolution - 0.5
    source_y = (source_north - target_y) / source_resolution - 0.5
    column0 = np.floor(source_x).astype(np.int64)
    row0 = np.floor(source_y).astype(np.int64)
    column1 = np.clip(column0 + 1, 0, source.shape[1] - 1)
    row1 = np.clip(row0 + 1, 0, source.shape[0] - 1)
    column0 = np.clip(column0, 0, source.shape[1] - 1)
    row0 = np.clip(row0, 0, source.shape[0] - 1)
    dx = source_x - np.floor(source_x)
    dy = source_y - np.floor(source_y)

    result = np.zeros(target_x.shape, dtype=np.float64)
    weight_sum = np.zeros(result.shape, dtype=np.float64)
    for rows, columns, weights in (
        (row0, column0, (1.0 - dy) * (1.0 - dx)),
        (row0, column1, (1.0 - dy) * dx),
        (row1, column0, dy * (1.0 - dx)),
        (row1, column1, dy * dx),
    ):
        values = source[rows, columns]
        valid = values != nodata
        result += np.where(valid, values * weights, 0.0)
        weight_sum += np.where(valid, weights, 0.0)
    return np.divide(
        result,
        weight_sum,
        out=np.full(result.shape, nodata, dtype=np.float64),
        where=weight_sum > 0.0,
    ).astype(np.float32)
