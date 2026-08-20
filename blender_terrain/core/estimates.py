"""Fast, dependency-free estimates for a rectangular WGS84 ROI."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..errors import UserInputError
from .roi import BBoxWGS84

_WGS84_AUTHALIC_RADIUS_METRES = 6_371_007.1809


@dataclass(frozen=True, slots=True)
class ROIEstimate:
    """Approximate physical size and source-grid demand for a WGS84 bbox."""

    width_metres: float
    height_metres: float
    area_square_metres: float
    sample_columns: int
    sample_rows: int

    @property
    def sample_count(self) -> int:
        """Return the total number of source samples."""

        return self.sample_columns * self.sample_rows


def estimate_bbox(bounds: BBoxWGS84, resolution_metres: float = 2.0) -> ROIEstimate:
    """Estimate bbox dimensions on an authalic WGS84 sphere."""

    if not math.isfinite(resolution_metres) or resolution_metres <= 0.0:
        raise UserInputError("Estimate resolution must be a positive finite value")

    longitude_span = math.radians(bounds.longitude_span)
    latitude_span = math.radians(bounds.latitude_span)
    middle_latitude = math.radians((bounds.south + bounds.north) / 2.0)
    south = math.radians(bounds.south)
    north = math.radians(bounds.north)

    width = _WGS84_AUTHALIC_RADIUS_METRES * math.cos(middle_latitude) * longitude_span
    height = _WGS84_AUTHALIC_RADIUS_METRES * latitude_span
    area = (
        _WGS84_AUTHALIC_RADIUS_METRES**2
        * longitude_span
        * (math.sin(north) - math.sin(south))
    )
    return ROIEstimate(
        width_metres=width,
        height_metres=height,
        area_square_metres=area,
        sample_columns=math.ceil(width / resolution_metres),
        sample_rows=math.ceil(height / resolution_metres),
    )
