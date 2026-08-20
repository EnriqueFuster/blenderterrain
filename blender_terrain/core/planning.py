"""Build a bounded, offline import plan from user-facing options."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..errors import PlanningLimitExceeded, UserInputError
from ..models import DatasetProduct
from .crs import UTMWorkArea, split_bbox_by_utm_zone
from .estimates import ROIEstimate, estimate_bbox
from .grid import DEFAULT_MAX_TILE_CELLS, GridSpec, align_projected_grid, tile_grid
from .projection import project_work_area_bounds
from .roi import BBoxWGS84

ELEVATION_RESOLUTIONS = (2.0, 5.0, 10.0, 20.0, 50.0, 100.0)
IMAGERY_RESOLUTIONS = (0.25, 0.5, 1.0, 2.0, 5.0)
MAX_ELEVATION_SAMPLES = 16_777_216
PLANNING_WMS_TILE_DIMENSION = 4_096
MAX_IMAGERY_PIXELS = 67_108_864


@dataclass(frozen=True, slots=True)
class ImageryEstimate:
    """Estimated PNOA output before live WMS capabilities are available."""

    gsd_metres: float
    pixel_width: int
    pixel_height: int
    tile_columns: int
    tile_rows: int

    @property
    def pixel_count(self) -> int:
        """Return the total number of requested texture pixels."""

        return self.pixel_width * self.pixel_height

    @property
    def tile_count(self) -> int:
        """Return the provisional number of WMS requests."""

        return self.tile_columns * self.tile_rows


@dataclass(frozen=True, slots=True)
class ImportPlan:
    """A validated offline plan suitable for later provider discovery."""

    bounds: BBoxWGS84
    work_areas: tuple[UTMWorkArea, ...]
    product: DatasetProduct
    elevation_resolution_metres: float
    elevation: ROIEstimate
    imagery: ImageryEstimate | None
    grids: tuple[GridSpec, ...]

    @property
    def crosses_utm_zones(self) -> bool:
        """Return whether processing must be divided by projected CRS."""

        return len(self.work_areas) > 1

    @property
    def terrain_tile_columns(self) -> int:
        """Return the provisional number of terrain objects from west to east."""

        return sum(
            math.ceil(grid.columns / DEFAULT_MAX_TILE_CELLS) for grid in self.grids
        )

    @property
    def terrain_tile_rows(self) -> int:
        """Return the provisional number of terrain objects from north to south."""

        return max(math.ceil(grid.rows / DEFAULT_MAX_TILE_CELLS) for grid in self.grids)

    @property
    def terrain_tile_count(self) -> int:
        """Return the provisional number of terrain objects."""

        return sum(len(tile_grid(grid)) for grid in self.grids)

    @property
    def elevation_sample_count(self) -> int:
        """Return the exact cell count after UTM projection and grid alignment."""

        return sum(grid.sample_count for grid in self.grids)


def create_import_plan(
    bounds: BBoxWGS84,
    product: DatasetProduct,
    elevation_resolution_metres: float | None,
    use_imagery: bool,
    imagery_gsd_metres: float | None,
) -> ImportPlan:
    """Validate output choices and calculate bounded elevation and imagery demand."""

    if product not in (DatasetProduct.MDT02, DatasetProduct.MDS02):
        raise UserInputError("Elevation product must be MDT02 or MDS02")
    work_areas = split_bbox_by_utm_zone(bounds)
    elevation_resolution, elevation, grids = _select_elevation_resolution(
        bounds, work_areas, elevation_resolution_metres
    )
    imagery = (
        _estimate_imagery(bounds, imagery_gsd_metres)
        if use_imagery
        else None
    )
    return ImportPlan(
        bounds=bounds,
        work_areas=work_areas,
        product=product,
        elevation_resolution_metres=elevation_resolution,
        elevation=elevation,
        imagery=imagery,
        grids=grids,
    )


def _select_elevation_resolution(
    bounds: BBoxWGS84,
    work_areas: tuple[UTMWorkArea, ...],
    requested: float | None,
) -> tuple[float, ROIEstimate, tuple[GridSpec, ...]]:
    candidates = ELEVATION_RESOLUTIONS if requested is None else (requested,)
    if requested is not None and requested not in ELEVATION_RESOLUTIONS:
        raise UserInputError("Unsupported elevation resolution")
    for resolution in candidates:
        estimate = estimate_bbox(bounds, resolution)
        grids = tuple(
            align_projected_grid(project_work_area_bounds(work_area), resolution)
            for work_area in work_areas
        )
        if sum(grid.sample_count for grid in grids) <= MAX_ELEVATION_SAMPLES:
            return resolution, estimate, grids
    raise PlanningLimitExceeded(
        "Elevation output exceeds the safe sample limit even at 100 m"
        if requested is None
        else "Elevation output exceeds the safe sample limit; use Auto or a coarser resolution"
    )


def _estimate_imagery(
    bounds: BBoxWGS84, requested_gsd: float | None
) -> ImageryEstimate:
    physical = estimate_bbox(bounds)
    candidates = IMAGERY_RESOLUTIONS if requested_gsd is None else (requested_gsd,)
    if requested_gsd is not None and requested_gsd not in IMAGERY_RESOLUTIONS:
        raise UserInputError("Unsupported imagery resolution")
    for gsd in candidates:
        pixel_width = math.ceil(physical.width_metres / gsd)
        pixel_height = math.ceil(physical.height_metres / gsd)
        estimate = ImageryEstimate(
            gsd_metres=gsd,
            pixel_width=pixel_width,
            pixel_height=pixel_height,
            tile_columns=math.ceil(pixel_width / PLANNING_WMS_TILE_DIMENSION),
            tile_rows=math.ceil(pixel_height / PLANNING_WMS_TILE_DIMENSION),
        )
        if estimate.pixel_count <= MAX_IMAGERY_PIXELS:
            return estimate
    raise PlanningLimitExceeded(
        "PNOA output exceeds the safe texture limit even at 5 m GSD"
        if requested_gsd is None
        else "PNOA output exceeds the safe texture limit; use Auto or a coarser GSD"
    )
