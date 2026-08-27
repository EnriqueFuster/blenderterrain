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
    tile_grid_manual,
)
from .heightmap import ElevationRange, calculate_elevation_range, normalize_heightmap
from .imagery import ImageryTileRequest, plan_imagery_tiles
from .mesh_geometry import (
    DEFAULT_PREVIEW_SUBDIVISION_LEVEL,
    PREVIEW_MESH_REDUCTION_FACTOR,
    TerrainMeshGeometry,
    build_displacement_mesh_geometry,
    build_terrain_mesh_geometry,
)
from .planning import ImageryEstimate, ImportPlan, create_import_plan
from .projection import (
    GeographicPoint,
    ProjectedPoint,
    project_utm_to_wgs84,
    project_wgs84_to_utm,
    project_work_area_bounds,
)
from .roi import BBoxWGS84, PolygonWGS84, RegionOfInterest, closed_ring
from .roi_input import bbox_from_center_size, format_bbox, parse_bbox
from .terrain_schema import (
    MAX_SUBDIVISION_LEVEL,
    SUBDIVISION_WARNING_LEVEL,
    TERRAIN_SCHEMA_VERSION,
    TerrainMetadata,
    TerrainRepresentation,
    TerrainSettings,
    read_terrain_metadata,
    subdivision_risk_message,
)
from .territory import TerritoryGroup, classify_territory_envelope
from .texture_mapping import (
    TextureTransform,
    bounds_fully_covered,
    projected_texture_transform,
)

__all__ = [
    "DEFAULT_MAX_TILE_CELLS",
    "DEFAULT_PREVIEW_SUBDIVISION_LEVEL",
    "MAX_SUBDIVISION_LEVEL",
    "PREVIEW_MESH_REDUCTION_FACTOR",
    "SUBDIVISION_WARNING_LEVEL",
    "TERRAIN_SCHEMA_VERSION",
    "BBoxWGS84",
    "CRSInfo",
    "DeliveryResult",
    "DiscoveryResult",
    "ElevationRange",
    "GeographicPoint",
    "GridSpec",
    "GridTile",
    "ImageryEstimate",
    "ImageryTileRequest",
    "ImportPlan",
    "PolygonWGS84",
    "ProcessedElevationTile",
    "ProjectedPoint",
    "ROIEstimate",
    "RegionOfInterest",
    "TerrainMeshGeometry",
    "TerrainMetadata",
    "TerrainRepresentation",
    "TerrainSettings",
    "TerritoryGroup",
    "TextureTransform",
    "TransferProgress",
    "UTMWorkArea",
    "align_projected_grid",
    "bbox_from_center_size",
    "bounds_fully_covered",
    "build_displacement_mesh_geometry",
    "build_terrain_mesh_geometry",
    "calculate_elevation_range",
    "classify_territory_envelope",
    "closed_ring",
    "create_import_plan",
    "deliver_plan_sources",
    "discover_sources",
    "estimate_bbox",
    "format_bbox",
    "normalize_heightmap",
    "parse_bbox",
    "plan_imagery_tiles",
    "process_elevation_tiles",
    "project_utm_to_wgs84",
    "project_wgs84_to_utm",
    "project_work_area_bounds",
    "projected_texture_transform",
    "read_terrain_metadata",
    "select_catalog_items",
    "split_bbox_by_utm_zone",
    "subdivision_risk_message",
    "tile_grid",
    "tile_grid_manual",
]
