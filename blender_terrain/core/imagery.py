"""Build exact projected PNOA requests from a validated import plan."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import PlanningLimitExceeded
from ..models import ProjectedBounds
from .grid import align_projected_grid, tile_grid
from .planning import MAX_IMAGERY_PIXELS, PLANNING_WMS_TILE_DIMENSION, ImportPlan


@dataclass(frozen=True, slots=True)
class ImageryTileRequest:
    """One projected, north-up PNOA GetMap request."""

    zone_index: int
    row: int
    column: int
    bounds: ProjectedBounds
    width: int
    height: int
    gsd_metres: float

    @property
    def filename(self) -> str:
        """Return a deterministic filename within one import request."""

        gsd = f"{self.gsd_metres:g}".replace(".", "p")
        return (
            f"pnoa_epsg{self.bounds.epsg}_z{self.zone_index}_"
            f"r{self.row}_c{self.column}_{gsd}m.png"
        )


def plan_imagery_tiles(plan: ImportPlan) -> tuple[ImageryTileRequest, ...]:
    """Split every projected work area into bounded PNOA WMS requests."""

    if plan.imagery is None:
        return ()
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
                )
            )
    if sum(request.width * request.height for request in requests) > MAX_IMAGERY_PIXELS:
        raise PlanningLimitExceeded("Exact projected PNOA tiles exceed the safe pixel limit")
    return tuple(requests)
