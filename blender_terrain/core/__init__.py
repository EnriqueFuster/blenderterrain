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
    TerrainMeshGeometry,
    build_displacement_mesh_geometry,
    build_terrain_mesh_geometry,
)
from .planning import ImageryEstimate, ImportPlan, create_import_plan
from .projection import ProjectedPoint, project_wgs84_to_utm, project_work_area_bounds
from .roi import BBoxWGS84
from .terrain_schema import (
    TERRAIN_SCHEMA_VERSION,
    TerrainMetadata,
    TerrainRepresentation,
    TerrainSettings,
    read_terrain_metadata,
)
from .territory import TerritoryGroup, classify_territory_envelope
from .texture_mapping import (
    TextureTransform,
    bounds_fully_covered,
    projected_texture_transform,
)

__all__ = [
    "DEFAULT_MAX_TILE_CELLS",
    "TERRAIN_SCHEMA_VERSION",
    "BBoxWGS84",
    "CRSInfo",
    "DeliveryResult",
    "DiscoveryResult",
    "ElevationRange",
    "GridSpec",
    "GridTile",
    "ImageryEstimate",
    "ImageryTileRequest",
    "ImportPlan",
    "ProcessedElevationTile",
    "ProjectedPoint",
    "ROIEstimate",
    "TerrainMeshGeometry",
    "TerrainMetadata",
    "TerrainRepresentation",
    "TerrainSettings",
    "TerritoryGroup",
    "TextureTransform",
    "TransferProgress",
    "UTMWorkArea",
    "align_projected_grid",
    "bounds_fully_covered",
    "build_displacement_mesh_geometry",
    "build_terrain_mesh_geometry",
    "calculate_elevation_range",
    "classify_territory_envelope",
    "create_import_plan",
    "deliver_plan_sources",
    "discover_sources",
    "estimate_bbox",
    "normalize_heightmap",
    "plan_imagery_tiles",
    "process_elevation_tiles",
    "project_wgs84_to_utm",
    "project_work_area_bounds",
    "projected_texture_transform",
    "read_terrain_metadata",
    "select_catalog_items",
    "split_bbox_by_utm_zone",
    "tile_grid",
    "tile_grid_manual",
]
