"""Build a bounded, offline import plan from user-facing options."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..errors import PlanningLimitExceeded, RasterAlignmentError, UserInputError
from ..models import DatasetProduct, ProjectedBounds
from .crs import UTMWorkArea, split_bbox_by_utm_zone, split_bbox_by_wgs84_utm_zone
from .estimates import ROIEstimate, estimate_bbox
from .grid import (
    DEFAULT_MAX_TILE_CELLS,
    GridSpec,
    GridTile,
    align_projected_grid,
    tile_grid,
    tile_grid_manual,
)
from .projection import project_work_area_bounds
from .roi import BBoxWGS84

ELEVATION_RESOLUTIONS = (0.5, 2.0, 5.0, 10.0, 20.0, 25.0, 50.0, 100.0, 200.0)
PRODUCT_NATIVE_RESOLUTION = {
    DatasetProduct.MDT50CM: 0.5,
    DatasetProduct.MDT02: 2.0,
    DatasetProduct.MDT05: 5.0,
    DatasetProduct.MDT25: 25.0,
    DatasetProduct.MDT200: 200.0,
    DatasetProduct.MDS50CM: 0.5,
    DatasetProduct.MDS02: 2.0,
    DatasetProduct.MDS05: 5.0,
}
IMAGERY_RESOLUTIONS = (0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0)
MAX_ELEVATION_SAMPLES = 16_777_216
PLANNING_WMS_TILE_DIMENSION = 4_096
MAX_IMAGERY_PIXELS = 67_108_864
ELEVATION_WORKING_BYTES_PER_SAMPLE = 11
IMAGERY_DECODED_BYTES_PER_PIXEL = 4
RESOURCE_PROFILES = {
    "CONSERVATIVE": (4_194_304, 16_777_216),
    "BALANCED": (MAX_ELEVATION_SAMPLES, MAX_IMAGERY_PIXELS),
    "LARGE": (67_108_864, 268_435_456),
}


@dataclass(frozen=True, slots=True)
class ImageryEstimate:
    """Estimated imagery output before live provider discovery."""

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
    product: DatasetProduct | str
    elevation_resolution_metres: float
    elevation: ROIEstimate
    imagery: ImageryEstimate | None
    grids: tuple[GridSpec, ...]
    manual_tile_rows: int | None = None
    manual_tile_columns: int | None = None

    @property
    def crosses_utm_zones(self) -> bool:
        """Return whether processing must be divided by projected CRS."""

        return len(self.work_areas) > 1

    @property
    def terrain_tile_columns(self) -> int:
        """Return the provisional number of terrain objects from west to east."""

        if self.manual_tile_columns is not None:
            return self.manual_tile_columns * len(self.grids)
        return sum(math.ceil(grid.columns / DEFAULT_MAX_TILE_CELLS) for grid in self.grids)

    @property
    def terrain_tile_rows(self) -> int:
        """Return the provisional number of terrain objects from north to south."""

        if self.manual_tile_rows is not None:
            return self.manual_tile_rows
        return max(math.ceil(grid.rows / DEFAULT_MAX_TILE_CELLS) for grid in self.grids)

    @property
    def terrain_tile_count(self) -> int:
        """Return the provisional number of terrain objects."""

        return sum(len(self.tiles_for_grid(index)) for index in range(len(self.grids)))

    def tiles_for_grid(self, grid_index: int) -> tuple[GridTile, ...]:
        """Return automatic or explicitly configured tiles for one projected grid."""

        grid = self.grids[grid_index]
        if self.manual_tile_rows is None or self.manual_tile_columns is None:
            return tile_grid(grid)
        return tile_grid_manual(grid, self.manual_tile_rows, self.manual_tile_columns)

    @property
    def elevation_sample_count(self) -> int:
        """Return the exact cell count after UTM projection and grid alignment."""

        return sum(grid.sample_count for grid in self.grids)

    @property
    def estimated_elevation_working_bytes(self) -> int:
        """Estimate elevation arrays, mask, provenance, and one Float32 scratch grid."""

        return self.elevation_sample_count * ELEVATION_WORKING_BYTES_PER_SAMPLE

    @property
    def estimated_imagery_decoded_bytes(self) -> int:
        """Estimate a four-byte decoded color buffer, excluding GPU copies."""

        return (
            0
            if self.imagery is None
            else self.imagery.pixel_count * IMAGERY_DECODED_BYTES_PER_PIXEL
        )

    @property
    def estimated_combined_bytes(self) -> int:
        """Return a lower-bound combined planning footprint."""

        return self.estimated_elevation_working_bytes + self.estimated_imagery_decoded_bytes

    @property
    def warnings(self) -> tuple[str, ...]:
        """Return actionable limitations discovered during offline planning."""

        warnings: list[str] = []
        if self.crosses_utm_zones:
            warnings.append("ROI crosses UTM zones and will create sibling terrain groups")
            if self.manual_tile_rows is not None:
                warnings.append("Manual terrain rows and columns apply separately to each CRS")
        warnings.append("Exact data coverage is confirmed during provider discovery")
        return tuple(warnings)


def create_import_plan(
    bounds: BBoxWGS84,
    product: DatasetProduct | str,
    elevation_resolution_metres: float | None,
    use_imagery: bool,
    imagery_gsd_metres: float | None,
    manual_tile_rows: int | None = None,
    manual_tile_columns: int | None = None,
    maximum_elevation_samples: int = MAX_ELEVATION_SAMPLES,
    maximum_imagery_pixels: int = MAX_IMAGERY_PIXELS,
    native_resolution_override: float | None = None,
    projected_bounds_override: tuple[ProjectedBounds, ...] | None = None,
    use_global_utm: bool = False,
    imagery_native_resolution_metres: float = 0.25,
) -> ImportPlan:
    """Validate output choices and calculate bounded elevation and imagery demand."""

    if maximum_elevation_samples <= 0 or maximum_imagery_pixels <= 0:
        raise UserInputError("Resource limits must be positive")
    if native_resolution_override is None:
        if not isinstance(product, DatasetProduct) or product not in PRODUCT_NATIVE_RESOLUTION:
            raise UserInputError(
                "A catalog product requires an explicit native elevation resolution"
            )
        native_resolution = PRODUCT_NATIVE_RESOLUTION[product]
    else:
        native_resolution = native_resolution_override
    if not math.isfinite(native_resolution) or native_resolution <= 0.0:
        raise UserInputError("Native elevation resolution must be positive")
    _validate_manual_tiles(manual_tile_rows, manual_tile_columns)
    work_areas = (
        split_bbox_by_wgs84_utm_zone(bounds)
        if use_global_utm
        else split_bbox_by_utm_zone(bounds)
    )
    if projected_bounds_override is not None and {
        projected.epsg for projected in projected_bounds_override
    } != {area.crs.epsg for area in work_areas}:
        raise UserInputError("Local raster CRS coverage does not match its WGS84 envelope")
    elevation_resolution, elevation, grids = _select_elevation_resolution(
        bounds,
        work_areas,
        elevation_resolution_metres,
        native_resolution,
        manual_tile_rows,
        manual_tile_columns,
        maximum_elevation_samples,
        projected_bounds_override,
    )
    imagery = (
        _estimate_imagery(
            bounds,
            imagery_gsd_metres,
            maximum_imagery_pixels,
            imagery_native_resolution_metres,
        )
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
        manual_tile_rows=manual_tile_rows,
        manual_tile_columns=manual_tile_columns,
    )


def _validate_manual_tiles(rows: int | None, columns: int | None) -> None:
    if (rows is None) != (columns is None):
        raise UserInputError("Manual terrain rows and columns must be provided together")
    if rows is None or columns is None:
        return
    if (
        isinstance(rows, bool)
        or isinstance(columns, bool)
        or not 1 <= rows <= 64
        or not 1 <= columns <= 64
    ):
        raise UserInputError("Manual terrain rows and columns must be between 1 and 64")


def _select_elevation_resolution(
    bounds: BBoxWGS84,
    work_areas: tuple[UTMWorkArea, ...],
    requested: float | None,
    native_resolution: float,
    manual_tile_rows: int | None,
    manual_tile_columns: int | None,
    maximum_samples: int,
    projected_bounds_override: tuple[ProjectedBounds, ...] | None,
) -> tuple[float, ROIEstimate, tuple[GridSpec, ...]]:
    available = tuple(
        sorted(
            {
                native_resolution,
                *(value for value in ELEVATION_RESOLUTIONS if value >= native_resolution),
            }
        )
    )
    candidates = available if requested is None else (requested,)
    if requested is not None and requested not in available:
        raise UserInputError("Unsupported elevation resolution")
    for resolution in candidates:
        estimate = estimate_bbox(bounds, resolution)
        if projected_bounds_override is None:
            grids = tuple(
                align_projected_grid(project_work_area_bounds(work_area), resolution)
                for work_area in work_areas
            )
        else:
            try:
                grids = tuple(
                    _exact_local_grid(projected, resolution)
                    for projected in sorted(
                        projected_bounds_override, key=lambda bounds: bounds.epsg
                    )
                )
            except RasterAlignmentError:
                continue
        if (
            sum(grid.sample_count for grid in grids) <= maximum_samples
            and _manual_layout_is_safe(grids, manual_tile_rows, manual_tile_columns)
        ):
            return resolution, estimate, grids
    raise PlanningLimitExceeded(
        "Elevation output or manual terrain layout exceeds safety limits even at the "
        "coarsest supported resolution"
        if requested is None
        else (
            "Elevation output or manual terrain layout exceeds safety limits; "
            "use Auto, a coarser resolution, or more terrain tiles"
        )
    )


def _exact_local_grid(bounds: ProjectedBounds, resolution: float) -> GridSpec:
    """Build a source-anchored grid without expanding beyond local raster coverage."""

    columns = round((bounds.east - bounds.west) / resolution)
    rows = round((bounds.north - bounds.south) / resolution)
    return GridSpec(bounds, resolution, columns, rows)


def _manual_layout_is_safe(
    grids: tuple[GridSpec, ...], rows: int | None, columns: int | None
) -> bool:
    if rows is None or columns is None:
        return True
    try:
        for grid in grids:
            tile_grid_manual(grid, rows, columns)
    except RasterAlignmentError:
        return False
    return True


def _estimate_imagery(
    bounds: BBoxWGS84,
    requested_gsd: float | None,
    maximum_pixels: int,
    native_resolution: float,
) -> ImageryEstimate:
    if not math.isfinite(native_resolution) or native_resolution <= 0.0:
        raise UserInputError("Native imagery resolution must be positive")
    physical = estimate_bbox(bounds)
    candidates = (
        tuple(gsd for gsd in IMAGERY_RESOLUTIONS if gsd >= native_resolution)
        if requested_gsd is None
        else (requested_gsd,)
    )
    if requested_gsd is not None and requested_gsd not in IMAGERY_RESOLUTIONS:
        raise UserInputError("Unsupported imagery resolution")
    if requested_gsd is not None and requested_gsd < native_resolution:
        raise UserInputError("Imagery resolution cannot be finer than the source data")
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
        if estimate.pixel_count <= maximum_pixels:
            return estimate
    raise PlanningLimitExceeded(
        "Imagery output exceeds the safe texture limit even at 100 m GSD"
        if requested_gsd is None
        else "Imagery output exceeds the safe texture limit; use Auto or a coarser GSD"
    )
