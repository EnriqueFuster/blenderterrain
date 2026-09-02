"""Build projected texture grids from a validated import plan."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import PlanningLimitExceeded
from ..models import ProjectedBounds
from .grid import align_projected_grid, tile_grid
from .planning import PLANNING_WMS_TILE_DIMENSION, ImportPlan


@dataclass(frozen=True, slots=True)
class ImageryTileRequest:
    """One projected, north-up texture tile request."""

    zone_index: int
    row: int
    column: int
    bounds: ProjectedBounds
    width: int
    height: int
    gsd_metres: float
    filename_prefix: str

    @property
    def filename(self) -> str:
        """Return a deterministic filename within one import request."""

        gsd = f"{self.gsd_metres:g}".replace(".", "p")
        return (
            f"{self.filename_prefix}_epsg{self.bounds.epsg}_z{self.zone_index}_"
            f"r{self.row}_c{self.column}_{gsd}m.png"
        )


def plan_texture_tiles(
    plan: ImportPlan, filename_prefix: str
) -> tuple[ImageryTileRequest, ...]:
    """Split every projected work area into bounded texture tiles."""

    if plan.imagery is None:
        return ()
    if not filename_prefix or not filename_prefix.replace("_", "").isalnum():
        raise ValueError(
            "Texture filename prefix must contain only letters, numbers, or underscores"
        )
    requests: list[ImageryTileRequest] = []
    for zone_index, elevation_grid in enumerate(plan.grids):
        grid = align_projected_grid(elevation_grid.bounds, plan.imagery.gsd_metres)
        for tile in tile_grid(grid, PLANNING_WMS_TILE_DIMENSION):
            requests.append(
                ImageryTileRequest(
                    zone_index=zone_index,
                    row=tile.row,
                    column=tile.column,
                    bounds=tile.bounds,
                    width=tile.columns,
                    height=tile.rows,
                    gsd_metres=plan.imagery.gsd_metres,
                    filename_prefix=filename_prefix,
                )
            )
    exact_pixels = sum(request.width * request.height for request in requests)
    if exact_pixels > plan.maximum_imagery_pixels:
        raise PlanningLimitExceeded(
            "Exact projected texture output requires "
            f"{exact_pixels:,} pixels but the selected resource profile allows "
            f"{plan.maximum_imagery_pixels:,}; use a coarser imagery resolution"
        )
    return tuple(requests)


def plan_imagery_tiles(plan: ImportPlan) -> tuple[ImageryTileRequest, ...]:
    """Return PNOA-named tiles for compatibility with the former API."""

    return plan_texture_tiles(plan, "pnoa")
