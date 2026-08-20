"""Validated region-of-interest geometry in WGS84 longitude and latitude."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..errors import UserInputError


@dataclass(frozen=True, slots=True)
class BBoxWGS84:
    """A non-wrapping WGS84 bounding box in longitude-latitude order."""

    west: float
    south: float
    east: float
    north: float

    def __post_init__(self) -> None:
        coordinates = (self.west, self.south, self.east, self.north)
        if not all(math.isfinite(value) for value in coordinates):
            raise UserInputError("WGS84 bounds must contain only finite coordinates")
        if not -180.0 <= self.west <= 180.0 or not -180.0 <= self.east <= 180.0:
            raise UserInputError("WGS84 longitude must be between -180 and 180 degrees")
        if not -90.0 <= self.south <= 90.0 or not -90.0 <= self.north <= 90.0:
            raise UserInputError("WGS84 latitude must be between -90 and 90 degrees")
        if self.east <= self.west or self.north <= self.south:
            raise UserInputError("WGS84 bounds must have positive width and height")

    @property
    def longitude_span(self) -> float:
        """Return the longitude span in degrees."""

        return self.east - self.west

    @property
    def latitude_span(self) -> float:
        """Return the latitude span in degrees."""

        return self.north - self.south

    def polygon_ring(self) -> tuple[tuple[float, float], ...]:
        """Return a closed counterclockwise exterior ring."""

        return (
            (self.west, self.south),
            (self.east, self.south),
            (self.east, self.north),
            (self.west, self.north),
            (self.west, self.south),
        )
