"""Portable geographic domain logic."""

from .bathymetry_processing import (
    ComposedTerrainTile,
    ProcessedBathymetryTile,
    compose_terrain_bathymetry,
    process_gebco_tiles,
)
from .crs import CRSInfo, ProjectedWorkArea, UTMWorkArea, split_bbox_by_utm_zone
from .delivery import TransferProgress
from .discovery import DiscoveryResult, discover_sources, select_catalog_items
from .elevation_processing import (
    ProcessedElevationTile,
    geographic_source_bounds,
    process_elevation_tiles,
)
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
from .imagery import ImageryTileRequest, plan_imagery_tiles, plan_texture_tiles
from .imagery_processing import ProcessedImageryTile, process_worldcover_imagery
from .local_elevation import (
    LocalElevationInspection,
    inspect_local_elevation,
    resolve_local_elevation_paths,
)
from .local_imagery import LocalImageryInspection, inspect_local_imagery
from .mesh_geometry import (
    DEFAULT_PREVIEW_SUBDIVISION_LEVEL,
    PREVIEW_MESH_REDUCTION_FACTOR,
    TerrainMeshGeometry,
    build_displacement_mesh_geometry,
    build_terrain_mesh_geometry,
    native_resolution_subdivision_level,
)
from .planning import RESOURCE_PROFILES, ImageryEstimate, ImportPlan, create_import_plan
from .prepared_export import PreparedRasterExport, export_prepared_rasters
from .projection import (
    GeographicPoint,
    ProjectedPoint,
    project_arrays_to_wgs84,
    project_to_wgs84,
    project_utm_to_wgs84,
    project_wgs84,
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
    "RESOURCE_PROFILES",
    "SUBDIVISION_WARNING_LEVEL",
    "TERRAIN_SCHEMA_VERSION",
    "BBoxWGS84",
    "CRSInfo",
    "ComposedTerrainTile",
    "DiscoveryResult",
    "ElevationRange",
    "GeographicPoint",
    "GridSpec",
    "GridTile",
    "ImageryEstimate",
    "ImageryTileRequest",
    "ImportPlan",
    "LocalElevationInspection",
    "LocalImageryInspection",
    "PolygonWGS84",
    "PreparedRasterExport",
    "ProcessedBathymetryTile",
    "ProcessedElevationTile",
    "ProcessedImageryTile",
    "ProjectedPoint",
    "ProjectedWorkArea",
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
    "compose_terrain_bathymetry",
    "create_import_plan",
    "discover_sources",
    "estimate_bbox",
    "export_prepared_rasters",
    "format_bbox",
    "geographic_source_bounds",
    "inspect_local_elevation",
    "inspect_local_imagery",
    "native_resolution_subdivision_level",
    "normalize_heightmap",
    "parse_bbox",
    "plan_imagery_tiles",
    "plan_texture_tiles",
    "process_elevation_tiles",
    "process_gebco_tiles",
    "process_worldcover_imagery",
    "project_arrays_to_wgs84",
    "project_to_wgs84",
    "project_utm_to_wgs84",
    "project_wgs84",
    "project_wgs84_to_utm",
    "project_work_area_bounds",
    "projected_texture_transform",
    "read_terrain_metadata",
    "resolve_local_elevation_paths",
    "select_catalog_items",
    "split_bbox_by_utm_zone",
    "subdivision_risk_message",
    "tile_grid",
    "tile_grid_manual",
]
