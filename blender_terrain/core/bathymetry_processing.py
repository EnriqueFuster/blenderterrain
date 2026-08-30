"""Reproject bathymetry and its quality grid onto planned terrain tiles."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from ..errors import RasterFormatError
from .elevation_processing import ProcessedElevationTile, process_elevation_tiles
from .grid import GridTile
from .planning import ImportPlan
from .roi import RegionOfInterest

GEBCO_TID_CODES = frozenset({0, *range(10, 18), *range(40, 49), *range(70, 73)})
COAST_TRANSITION_METRES = 64.0
WATER_LEVEL_TOLERANCE_METRES = 0.5


@dataclass(frozen=True, slots=True)
class ProcessedBathymetryTile:
    zone_index: int
    tile: GridTile
    elevation: NDArray[np.float32]
    tid: NDArray[np.uint8]
    nodata: float


@dataclass(frozen=True, slots=True)
class ComposedTerrainTile:
    zone_index: int
    tile: GridTile
    elevation: NDArray[np.float32]
    marine_mask: NDArray[np.uint8]
    nodata: float


def compose_terrain_bathymetry(
    terrain_tiles: tuple[ProcessedElevationTile, ...],
    bathymetry_tiles: tuple[ProcessedBathymetryTile, ...],
) -> tuple[ComposedTerrainTile, ...]:
    """Compose a scientific land-seabed grid using GEBCO TID, never elevation sign."""

    if len(terrain_tiles) != len(bathymetry_tiles):
        raise RasterFormatError("Terrain and bathymetry tile counts differ")
    outputs: list[ComposedTerrainTile] = []
    for terrain, bathymetry in zip(terrain_tiles, bathymetry_tiles, strict=True):
        same_grid = (
            terrain.zone_index == bathymetry.zone_index
            and terrain.tile == bathymetry.tile
            and terrain.data.shape == bathymetry.elevation.shape
        )
        if not same_grid:
            raise RasterFormatError("Terrain and bathymetry grids do not align")
        terrain_valid = terrain.data != terrain.nodata
        bathymetry_valid = bathymetry.elevation != bathymetry.nodata
        tid_marine = (bathymetry.tid != 0) & (bathymetry.tid != 255)
        source_water = (
            terrain_valid
            & tid_marine
            & (np.abs(terrain.data) <= WATER_LEVEL_TOLERANCE_METRES)
            & (bathymetry.elevation <= 0.0)
        )
        use_terrain = terrain_valid & ~source_water
        fallback = ~use_terrain & bathymetry_valid & (bathymetry.tid != 255)
        elevation = np.full(terrain.data.shape, terrain.nodata, dtype=np.float32)
        elevation[use_terrain] = terrain.data[use_terrain]
        elevation[fallback] = bathymetry.elevation[fallback]
        resolution = (terrain.tile.bounds.east - terrain.tile.bounds.west) / (
            terrain.tile.columns
        )
        _blend_fallback_boundary(
            elevation,
            bathymetry.elevation,
            use_terrain,
            fallback,
            max(1, min(64, round(COAST_TRANSITION_METRES / resolution))),
        )
        mask = np.full(bathymetry.tid.shape, 255, dtype=np.uint8)
        known = use_terrain | fallback
        marine = fallback & (bathymetry.elevation <= 0.0)
        mask[known] = 0
        mask[marine] = 1
        outputs.append(
            ComposedTerrainTile(
                bathymetry.zone_index,
                bathymetry.tile,
                elevation,
                mask,
                float(terrain.nodata),
            )
        )
    _stitch_tile_edges(outputs)
    return tuple(outputs)


def _blend_fallback_boundary(
    output: NDArray[np.float32],
    bathymetry: NDArray[np.float32],
    source: NDArray[np.bool_],
    fallback: NDArray[np.bool_],
    steps: int,
) -> None:
    """Blend fallback cells outwards from valid terrain without leaving gaps."""

    known = source.copy()
    propagated = output.copy()
    for step in range(1, steps + 1):
        neighbour_sum = np.zeros(output.shape, dtype=np.float32)
        neighbour_count = np.zeros(output.shape, dtype=np.uint8)
        neighbour_sum[1:] += np.where(known[:-1], propagated[:-1], 0.0)
        neighbour_count[1:] += known[:-1]
        neighbour_sum[:-1] += np.where(known[1:], propagated[1:], 0.0)
        neighbour_count[:-1] += known[1:]
        neighbour_sum[:, 1:] += np.where(known[:, :-1], propagated[:, :-1], 0.0)
        neighbour_count[:, 1:] += known[:, :-1]
        neighbour_sum[:, :-1] += np.where(known[:, 1:], propagated[:, 1:], 0.0)
        neighbour_count[:, :-1] += known[:, 1:]
        frontier = fallback & ~known & (neighbour_count > 0)
        if not frontier.any():
            break
        propagated[frontier] = neighbour_sum[frontier] / neighbour_count[frontier]
        fraction = step / steps
        weight = fraction * fraction * (3.0 - 2.0 * fraction)
        output[frontier] = (
            propagated[frontier] * (1.0 - weight)
            + bathymetry[frontier] * weight
        )
        known[frontier] = True


def _stitch_tile_edges(tiles: list[ComposedTerrainTile]) -> None:
    """Make duplicated nodes on adjacent terrain objects exactly identical."""

    by_position = {(tile.zone_index, tile.tile.row, tile.tile.column): tile for tile in tiles}
    for tile in tiles:
        right = by_position.get(
            (tile.zone_index, tile.tile.row, tile.tile.column + 1)
        )
        if right is not None:
            _merge_edge(
                tile.elevation[:, -1],
                right.elevation[:, 0],
                tile.marine_mask[:, -1],
                right.marine_mask[:, 0],
                tile.nodata,
            )
        below = by_position.get(
            (tile.zone_index, tile.tile.row + 1, tile.tile.column)
        )
        if below is not None:
            _merge_edge(
                tile.elevation[-1, :],
                below.elevation[0, :],
                tile.marine_mask[-1, :],
                below.marine_mask[0, :],
                tile.nodata,
            )
    corners: dict[tuple[int, float, float], list[tuple[ComposedTerrainTile, int, int]]] = {}
    for tile in tiles:
        for row, y in ((0, tile.tile.bounds.north), (-1, tile.tile.bounds.south)):
            for column, x in ((0, tile.tile.bounds.west), (-1, tile.tile.bounds.east)):
                corners.setdefault((tile.tile.bounds.epsg, x, y), []).append(
                    (tile, row, column)
                )
    for shared in corners.values():
        if len(shared) < 2:
            continue
        valid_values = [
            float(tile.elevation[row, column])
            for tile, row, column in shared
            if tile.elevation[row, column] != tile.nodata
        ]
        if not valid_values:
            continue
        value = float(np.mean(valid_values))
        marine = value <= 0.0 and any(
            tile.marine_mask[row, column] == 1 for tile, row, column in shared
        )
        for tile, row, column in shared:
            tile.elevation[row, column] = value
            tile.marine_mask[row, column] = 1 if marine else 0


def _merge_edge(
    first: NDArray[np.float32],
    second: NDArray[np.float32],
    first_mask: NDArray[np.uint8],
    second_mask: NDArray[np.uint8],
    nodata: float,
) -> None:
    if first.shape != second.shape:
        raise RasterFormatError("Adjacent terrain tile edges do not align")
    first_valid = first != nodata
    second_valid = second != nodata
    both = first_valid & second_valid
    merged = np.where(both, (first + second) * 0.5, np.where(first_valid, first, second))
    valid = first_valid | second_valid
    first[valid] = merged[valid]
    second[valid] = merged[valid]
    marine = valid & (merged <= 0.0) & ((first_mask == 1) | (second_mask == 1))
    quality = np.full(first.shape, 255, dtype=np.uint8)
    quality[valid] = 0
    quality[marine] = 1
    first_mask[:] = quality
    second_mask[:] = quality


def process_gebco_tiles(
    elevation_path: Path,
    tid_path: Path,
    plan: ImportPlan,
    progress_callback: Callable[[int, int], None] | None = None,
    region: RegionOfInterest | None = None,
) -> tuple[ProcessedBathymetryTile, ...]:
    """Reproject GEBCO elevation bilinearly and TID by nearest neighbour."""

    elevation = process_elevation_tiles((elevation_path,), plan, region=region)
    quality = process_elevation_tiles(
        (tid_path,), plan, sampling="nearest", region=region
    )
    if len(elevation) != len(quality):
        raise RasterFormatError("Processed GEBCO elevation and TID tile counts differ")
    outputs: list[ProcessedBathymetryTile] = []
    if progress_callback is not None:
        progress_callback(0, len(elevation))
    for bathymetry, tid_tile in zip(elevation, quality, strict=True):
        if bathymetry.tile != tid_tile.tile or bathymetry.zone_index != tid_tile.zone_index:
            raise RasterFormatError("Processed GEBCO elevation and TID grids do not align")
        raw_tid = tid_tile.data
        valid = raw_tid != tid_tile.nodata
        rounded = np.rint(raw_tid[valid])
        if not np.all(raw_tid[valid] == rounded) or not set(rounded.astype(int)).issubset(
            GEBCO_TID_CODES
        ):
            raise RasterFormatError("Processed GEBCO TID contains unknown quality codes")
        tid = np.full(raw_tid.shape, 255, dtype=np.uint8)
        tid[valid] = rounded.astype(np.uint8)
        outputs.append(
            ProcessedBathymetryTile(
                bathymetry.zone_index,
                bathymetry.tile,
                bathymetry.data,
                tid,
                bathymetry.nodata,
            )
        )
        if progress_callback is not None:
            progress_callback(len(outputs), len(elevation))
    return tuple(outputs)
