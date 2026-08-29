"""Reproject bathymetry and its quality grid onto planned terrain tiles."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from ..errors import RasterFormatError
from .elevation_processing import process_elevation_tiles
from .grid import GridTile
from .planning import ImportPlan

GEBCO_TID_CODES = frozenset({0, *range(10, 18), *range(40, 49), *range(70, 73)})


@dataclass(frozen=True, slots=True)
class ProcessedBathymetryTile:
    zone_index: int
    tile: GridTile
    elevation: NDArray[np.float32]
    tid: NDArray[np.uint8]
    nodata: float


def process_gebco_tiles(
    elevation_path: Path,
    tid_path: Path,
    plan: ImportPlan,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[ProcessedBathymetryTile, ...]:
    """Reproject GEBCO elevation bilinearly and TID by nearest neighbour."""

    elevation = process_elevation_tiles((elevation_path,), plan)
    quality = process_elevation_tiles((tid_path,), plan, sampling="nearest")
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
