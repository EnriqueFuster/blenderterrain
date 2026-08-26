"""Windowed elevation mosaicking and bilinear resampling for terrain tiles."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from ..errors import RasterFormatError
from ..io.bigtiff_tiles import BigTiffFloatTileReader
from ..io.elevation_mosaic import read_elevation_mosaic
from ..models import ProjectedBounds
from .grid import GridTile, tile_grid
from .planning import ImportPlan

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
) -> tuple[ProcessedElevationTile, ...]:
    """Build every planned terrain tile while bounding native source windows."""

    if maximum_source_window_pixels <= 0:
        raise ValueError("Maximum source window pixels must be positive")
    readers = tuple(BigTiffFloatTileReader(path) for path in source_paths)
    outputs: list[ProcessedElevationTile] = []
    total_tiles = sum(len(tile_grid(grid)) for grid in plan.grids)
    if progress_callback is not None:
        progress_callback(0, total_tiles)
    for zone_index, grid in enumerate(plan.grids):
        zone_readers = tuple(
            reader for reader in readers if reader.georeference.epsg == grid.bounds.epsg
        )
        if not zone_readers:
            raise RasterFormatError(f"No elevation source is available for EPSG:{grid.bounds.epsg}")
        for tile in tile_grid(grid):
            outputs.append(
                _resample_tile(
                    zone_index, tile, zone_readers, maximum_source_window_pixels
                )
            )
            if progress_callback is not None:
                progress_callback(len(outputs), total_tiles)
    return tuple(outputs)


def _resample_tile(
    zone_index: int,
    tile: GridTile,
    readers: tuple[BigTiffFloatTileReader, ...],
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
    nodata = readers[0].layout.nodata
    if nodata is None:
        raise RasterFormatError("Elevation sources must declare NoData")
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

    result = np.zeros((len(target_y), len(target_x)), dtype=np.float64)
    weight_sum = np.zeros(result.shape, dtype=np.float64)
    for rows, columns, weights in (
        (row0[:, None], column0[None, :], (1.0 - dy)[:, None] * (1.0 - dx)[None, :]),
        (row0[:, None], column1[None, :], (1.0 - dy)[:, None] * dx[None, :]),
        (row1[:, None], column0[None, :], dy[:, None] * (1.0 - dx)[None, :]),
        (row1[:, None], column1[None, :], dy[:, None] * dx[None, :]),
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
