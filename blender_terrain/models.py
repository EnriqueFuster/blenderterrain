"""Small immutable models shared across providers and processing pipelines."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProjectedBounds:
    """Rectangular bounds expressed in one projected EPSG coordinate system."""

    west: float
    south: float
    east: float
    north: float
    epsg: int

    def __post_init__(self) -> None:
        coordinates = (self.west, self.south, self.east, self.north)
        if not all(math.isfinite(value) for value in coordinates):
            raise ValueError("Projected bounds must contain only finite coordinates")
        if self.east <= self.west or self.north <= self.south:
            raise ValueError("Projected bounds must have positive width and height")
        if self.epsg <= 0:
            raise ValueError("EPSG code must be positive")
