"""Portable geographic domain logic."""

from .crs import CRSInfo, UTMWorkArea, split_bbox_by_utm_zone
from .delivery import DeliveryResult, TransferProgress, deliver_plan_sources
from .discovery import DiscoveryResult, discover_sources, select_catalog_items
from .elevation_processing import ProcessedElevationTile, process_elevation_tiles
from .estimates import ROIEstimate, estimate_bbox
from .grid import (
    DEFAULT_MAX_TILE_CELLS,
    GridSpec,
    GridTile,
    align_projected_grid,
    tile_grid,
)
from .imagery import ImageryTileRequest, plan_imagery_tiles
from .planning import ImageryEstimate, ImportPlan, create_import_plan
from .projection import ProjectedPoint, project_wgs84_to_utm, project_work_area_bounds
from .roi import BBoxWGS84
from .territory import TerritoryGroup, classify_territory_envelope

__all__ = [
    "DEFAULT_MAX_TILE_CELLS",
    "BBoxWGS84",
    "CRSInfo",
    "DeliveryResult",
    "DiscoveryResult",
    "GridSpec",
    "GridTile",
    "ImageryEstimate",
    "ImageryTileRequest",
    "ImportPlan",
    "ProcessedElevationTile",
    "ProjectedPoint",
    "ROIEstimate",
    "TerritoryGroup",
    "TransferProgress",
    "UTMWorkArea",
    "align_projected_grid",
    "classify_territory_envelope",
    "create_import_plan",
    "deliver_plan_sources",
    "discover_sources",
    "estimate_bbox",
    "plan_imagery_tiles",
    "process_elevation_tiles",
    "project_wgs84_to_utm",
    "project_work_area_bounds",
    "select_catalog_items",
    "split_bbox_by_utm_zone",
    "tile_grid",
]
