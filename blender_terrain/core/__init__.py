"""Portable geographic domain logic."""

from .crs import CRSInfo, UTMWorkArea, split_bbox_by_utm_zone
from .estimates import ROIEstimate, estimate_bbox
from .grid import (
    DEFAULT_MAX_TILE_CELLS,
    GridSpec,
    GridTile,
    align_projected_grid,
    tile_grid,
)
from .planning import ImageryEstimate, ImportPlan, create_import_plan
from .projection import ProjectedPoint, project_wgs84_to_utm, project_work_area_bounds
from .roi import BBoxWGS84

__all__ = [
    "DEFAULT_MAX_TILE_CELLS",
    "BBoxWGS84",
    "CRSInfo",
    "GridSpec",
    "GridTile",
    "ImageryEstimate",
    "ImportPlan",
    "ProjectedPoint",
    "ROIEstimate",
    "UTMWorkArea",
    "align_projected_grid",
    "create_import_plan",
    "estimate_bbox",
    "project_wgs84_to_utm",
    "project_work_area_bounds",
    "split_bbox_by_utm_zone",
    "tile_grid",
]
