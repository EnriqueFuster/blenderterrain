"""Shared-range Float32 heightmaps for non-destructive terrain displacement."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from ..errors import RasterFormatError


@dataclass(frozen=True, slots=True)
class ElevationRange:
    """One elevation baseline and range shared by every tile in an import."""

    minimum: float
    maximum: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.minimum) or not math.isfinite(self.maximum):
            raise ValueError("Elevation range must be finite")
        if self.maximum < self.minimum:
            raise ValueError("Elevation range maximum must not be below its minimum")

    @property
    def span(self) -> float:
        """Return a positive displacement strength, including flat terrain."""

        return max(self.maximum - self.minimum, float(np.finfo(np.float32).eps))


def calculate_elevation_range(
    tiles: tuple[tuple[NDArray[np.float32], float], ...],
) -> ElevationRange:
    """Calculate a common range while excluding each tile's NoData nodes."""

    minimum = math.inf
    maximum = -math.inf
    for elevation, nodata in tiles:
        _validate_elevation(elevation)
        valid = elevation != nodata
        if valid.any():
            minimum = min(minimum, float(elevation[valid].min()))
            maximum = max(maximum, float(elevation[valid].max()))
    if not math.isfinite(minimum) or not math.isfinite(maximum):
        raise RasterFormatError("Terrain elevation contains no valid height samples")
    return ElevationRange(minimum, maximum)


def normalize_heightmap(
    elevation: NDArray[np.float32], nodata: float, elevation_range: ElevationRange
) -> NDArray[np.float32]:
    """Normalize valid elevation into a shared zero-to-one displacement image."""

    _validate_elevation(elevation)
    valid = elevation != nodata
    normalized = np.zeros(elevation.shape, dtype=np.float32)
    normalized[valid] = (
        elevation[valid].astype(np.float64) - elevation_range.minimum
    ) / elevation_range.span
    if np.any(normalized[valid] < 0.0) or np.any(normalized[valid] > 1.0):
        raise RasterFormatError("Elevation tile lies outside the shared heightmap range")
    return normalized


def _validate_elevation(elevation: NDArray[np.float32]) -> None:
    if elevation.dtype != np.float32 or elevation.ndim != 2 or not elevation.size:
        raise RasterFormatError("Heightmap elevation must be a non-empty Float32 grid")
