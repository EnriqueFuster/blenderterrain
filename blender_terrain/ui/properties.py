"""Scene properties used to enter and validate a rectangular ROI."""

from __future__ import annotations

import json
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

from ..errors import BlenderTerrainError

_IMPORT_ITEMS_CACHE: list[tuple[str, str, str]] = []
_GPKG_LAYER_ITEMS_CACHE: list[tuple[str, str, str]] = []


def _terrain_import_items(
    properties: object, context: bpy.types.Context
) -> list[tuple[str, str, str]]:
    from .terrain_controls import import_items

    _IMPORT_ITEMS_CACHE[:] = import_items()
    return _IMPORT_ITEMS_CACHE


def _active_import_changed(properties: object, context: bpy.types.Context) -> None:
    from .terrain_controls import load_import_settings, sync_selected_settings

    load_import_settings(properties)
    sync_selected_settings(context, properties, force=True)


def _import_settings_tab_changed(properties: object, context: bpy.types.Context) -> None:
    if properties.import_settings_tab != "SELECTED":
        return
    from .terrain_controls import sync_selected_settings

    sync_selected_settings(context, properties, force=True)


def _invalidate_validation(properties: object, context: bpy.types.Context) -> None:
    """Mark estimates stale while preserving the current ROI definition."""

    if properties.internal_update:
        return
    properties.is_valid = False
    properties.validation_message = "Options changed; validate ROI again"
    properties.discovery_summary = ""
    properties.discovery_ready = False
    properties.delivery_ready = False
    properties.delivery_summary = ""
    properties.delivery_result_path = ""
    properties.terrain_created = False
    properties.import_id = ""
    properties.imagery_packed = False
    properties.imagery_available = False
    properties.imagery_size_mib = 0.0


def _roi_definition_changed(properties: object, context: bpy.types.Context) -> None:
    """Invalidate planning and discard geometry superseded by an ROI input change."""

    _invalidate_validation(properties, context)
    if not properties.internal_update:
        properties.roi_geometry_json = ""
        properties.product_availability_json = "[]"
        properties.product_availability_summary = ""


def _roi_file_changed(properties: object, context: bpy.types.Context) -> None:
    """Refresh lightweight GeoPackage layer metadata after choosing a file."""

    _invalidate_validation(properties, context)
    properties.roi_geometry_json = ""
    properties.product_availability_json = "[]"
    properties.product_availability_summary = ""
    properties.gpkg_layers_json = "[]"
    properties.gpkg_inspection_message = ""
    if Path(properties.roi_file_path).suffix.lower() != ".gpkg":
        return
    from ..io.geopackage import list_geopackage_polygon_layers

    try:
        path = Path(bpy.path.abspath(properties.roi_file_path))
        layers = list_geopackage_polygon_layers(path)
    except (BlenderTerrainError, OSError, ValueError) as error:
        properties.gpkg_inspection_message = str(error)
        return
    properties.gpkg_layers_json = json.dumps(
        [[layer.name, layer.geometry_type, layer.srs_id] for layer in layers]
    )
    properties.gpkg_inspection_message = (
        f"Found {len(layers)} polygon layer(s)"
        if layers
        else "GeoPackage contains no Polygon or MultiPolygon layers"
    )
    if layers:
        properties.gpkg_layer = layers[0].name


def _gpkg_layer_items(
    properties: object, context: bpy.types.Context
) -> list[tuple[str, str, str]]:
    try:
        layers = json.loads(properties.gpkg_layers_json)
    except (json.JSONDecodeError, TypeError):
        layers = []
    _GPKG_LAYER_ITEMS_CACHE[:] = [
        (str(name), str(name), f"{geometry_type}, SRS {srs_id}")
        for name, geometry_type, srs_id in layers
    ]
    if not _GPKG_LAYER_ITEMS_CACHE:
        _GPKG_LAYER_ITEMS_CACHE.append(("__NONE__", "No polygon layers", ""))
    return _GPKG_LAYER_ITEMS_CACHE


class BLENDERTERRAIN_ROIProperties(bpy.types.PropertyGroup):
    """Store manual WGS84 bounds and their latest validation result."""

    cache_cleanup_selection: EnumProperty(
        name="Remove",
        items=(
            ("PARTIALS", "Incomplete Files", "Remove interrupted .part files only"),
            ("PROCESSED", "Processed Terrain", "Remove generated elevation arrays"),
            ("JOBS", "Job History", "Remove persisted jobs, events, and worker logs"),
            ("IMAGERY", "PNOA Imagery", "Remove cached PNOA image tiles"),
            ("ELEVATION", "Elevation Sources", "Remove downloaded elevation rasters"),
            ("ALL", "All Cache Data", "Remove every BlenderTerrain cache category"),
        ),
        default="PARTIALS",
    )
    cache_inventory_json: StringProperty(default="[]", options={"HIDDEN"})
    cache_inventory_summary: StringProperty(
        default="Cache has not been inspected", options={"HIDDEN"}
    )

    elevation_source: EnumProperty(
        name="Elevation Source",
        items=(
            ("CNIG", "Download from CNIG", "Discover and download official elevation data"),
            ("LOCAL", "Local Raster", "Process a compatible local TIFF or a folder of TIFFs"),
        ),
        default="CNIG",
        update=_invalidate_validation,
    )
    local_elevation_path: StringProperty(
        name="Raster or Folder",
        description="Compatible elevation .tif/.tiff file or folder containing source tiles",
        subtype="FILE_PATH",
        update=_invalidate_validation,
    )

    data_settings_tab: EnumProperty(
        name="Data Settings",
        items=(
            ("ELEVATION", "Elevation", "Configure terrain elevation data"),
            ("IMAGERY", "Imagery", "Configure optional PNOA imagery"),
        ),
        default="ELEVATION",
    )

    roi_input_mode: EnumProperty(
        name="ROI Input",
        items=(
            ("BOUNDING_BOX", "Bounding Box", "Enter WGS84 rectangle coordinates"),
            ("CENTER_SIZE", "Center + Size", "Enter a WGS84 centre and metric dimensions"),
            ("FILE", "Polygon File", "Load Polygon or MultiPolygon geometry from a GIS file"),
            ("MAP_RECTANGLE", "Draw Rectangle on Map", "Draw a rectangle in the browser map"),
            ("MAP_POLYGON", "Draw Polygon on Map", "Draw a polygon in the browser map"),
        ),
        default="BOUNDING_BOX",
        update=_roi_definition_changed,
    )
    west: FloatProperty(
        name="West", default=-0.39, min=-180.0, max=180.0, precision=6,
        update=_roi_definition_changed,
    )
    south: FloatProperty(
        name="South", default=39.46, min=-90.0, max=90.0, precision=6,
        update=_roi_definition_changed,
    )
    east: FloatProperty(
        name="East", default=-0.37, min=-180.0, max=180.0, precision=6,
        update=_roi_definition_changed,
    )
    north: FloatProperty(
        name="North", default=39.48, min=-90.0, max=90.0, precision=6,
        update=_roi_definition_changed,
    )
    center_longitude: FloatProperty(
        name="Longitude", default=-0.38, min=-180.0, max=180.0, precision=6,
        update=_roi_definition_changed,
    )
    center_latitude: FloatProperty(
        name="Latitude", default=39.47, min=-90.0, max=90.0, precision=6,
        update=_roi_definition_changed,
    )
    roi_width_metres: FloatProperty(
        name="Width (m)", default=2_000.0, min=1.0, max=1_000_000.0,
        update=_roi_definition_changed,
    )
    roi_height_metres: FloatProperty(
        name="Height (m)", default=2_000.0, min=1.0, max=1_000_000.0,
        update=_roi_definition_changed,
    )
    roi_file_path: StringProperty(
        name="ROI File",
        description="GeoJSON, KML, Shapefile (.shp with .prj), or GeoPackage polygon file",
        subtype="FILE_PATH",
        update=_roi_file_changed,
    )
    gpkg_layers_json: StringProperty(default="[]", options={"HIDDEN"})
    gpkg_inspection_message: StringProperty(default="", options={"HIDDEN"})
    gpkg_layer: EnumProperty(
        name="Polygon Layer",
        items=_gpkg_layer_items,
        update=_roi_definition_changed,
    )
    roi_geometry_json: StringProperty(default="", options={"HIDDEN"})
    internal_update: BoolProperty(default=False, options={"HIDDEN"})
    product: EnumProperty(
        name="Elevation Product",
        items=(
            ("MDT50CM", "DTM (MDT50 cm, 3rd)", "0.5 m terrain; coverage is incomplete"),
            ("MDT02", "DTM (MDT02, 2nd)", "2 m bare-earth terrain"),
            ("MDT05", "DTM (MDT05, 1st)", "5 m bare-earth terrain"),
            ("MDT25", "DTM (MDT25, 2nd)", "25 m bare-earth terrain"),
            ("MDT200", "DTM (MDT200, 2nd)", "200 m bare-earth terrain"),
            ("MDS50CM", "DSM (MDS50 cm, 3rd)", "0.5 m surface; coverage is incomplete"),
            ("MDS02", "DSM (MDS02, 2nd)", "2 m buildings and vegetation"),
            ("MDS05", "DSM (MDS05, 1st)", "5 m buildings and vegetation"),
        ),
        default="MDT02",
        update=_invalidate_validation,
    )
    elevation_resolution: EnumProperty(
        name="Elevation Resolution",
        items=(
            ("AUTO", "Auto", "Choose the finest safe resolution"),
            *tuple(
                (str(value), f"{value} m", "Output grid spacing")
                for value in (0.5, 2, 5, 10, 20, 25, 50, 100, 200)
            ),
        ),
        default="AUTO",
        update=_invalidate_validation,
    )
    resource_profile: EnumProperty(
        name="Resource Profile",
        items=(
            ("CONSERVATIVE", "Conservative", "Lower RAM and GPU limits"),
            ("BALANCED", "Balanced", "Recommended limits for most workstations"),
            ("LARGE", "Large", "Higher limits; may exhaust RAM or GPU memory"),
        ),
        default="BALANCED",
        update=_invalidate_validation,
    )
    tiling_mode: EnumProperty(
        name="Terrain Division",
        items=(
            ("AUTOMATIC", "Automatic", "Choose safe terrain object dimensions"),
            ("MANUAL", "Manual Grid", "Set exact rows and columns per projected CRS"),
        ),
        default="AUTOMATIC",
        update=_invalidate_validation,
    )
    manual_tile_rows: IntProperty(
        name="Rows", default=1, min=1, max=64, update=_invalidate_validation
    )
    manual_tile_columns: IntProperty(
        name="Columns", default=1, min=1, max=64, update=_invalidate_validation
    )
    use_imagery: BoolProperty(
        name="Use PNOA Orthophoto", default=True, update=_invalidate_validation
    )
    imagery_gsd: EnumProperty(
        name="Imagery GSD",
        items=(
            ("AUTO", "Auto", "Choose the finest safe GSD"),
            *tuple(
                (str(value), f"{value} m", "Texture ground sample distance")
                for value in (0.25, 0.5, 1, 2, 5)
            ),
        ),
        default="AUTO",
        update=_invalidate_validation,
    )

    is_valid: BoolProperty(default=False, options={"HIDDEN"})
    validation_message: StringProperty(default="ROI has not been validated", options={"HIDDEN"})
    crs_summary: StringProperty(default="", options={"HIDDEN"})
    area_square_metres: FloatProperty(default=0.0, options={"HIDDEN"})
    sample_count: IntProperty(default=0, min=0, options={"HIDDEN"})
    selected_resolution: FloatProperty(default=0.0, options={"HIDDEN"})
    imagery_summary: StringProperty(default="", options={"HIDDEN"})
    product_availability_json: StringProperty(default="[]", options={"HIDDEN"})
    product_availability_summary: StringProperty(default="", options={"HIDDEN"})
    terrain_tile_count: IntProperty(default=0, min=0, options={"HIDDEN"})
    terrain_tile_summary: StringProperty(default="", options={"HIDDEN"})
    estimated_memory_mib: FloatProperty(default=0.0, min=0.0, options={"HIDDEN"})
    estimated_base_vertices: IntProperty(default=0, min=0, options={"HIDDEN"})
    estimated_texture_gpu_mib: FloatProperty(default=0.0, min=0.0, options={"HIDDEN"})
    planning_warning: StringProperty(default="", options={"HIDDEN"})
    job_active: BoolProperty(default=False, options={"HIDDEN"})
    active_job_mode: StringProperty(default="", options={"HIDDEN"})
    job_state: StringProperty(default="", options={"HIDDEN"})
    job_progress: FloatProperty(
        default=0.0, min=0.0, max=1.0, subtype="FACTOR", options={"HIDDEN"}
    )
    job_message: StringProperty(default="", options={"HIDDEN"})
    job_event_history: StringProperty(default="[]", options={"HIDDEN"})
    last_job_path: StringProperty(default="", options={"HIDDEN"})
    last_job_mode: StringProperty(default="", options={"HIDDEN"})
    discovered_file_count: IntProperty(default=0, min=0, options={"HIDDEN"})
    estimated_download_mb: FloatProperty(default=0.0, min=0.0, options={"HIDDEN"})
    discovery_summary: StringProperty(default="", options={"HIDDEN"})
    discovery_ready: BoolProperty(default=False, options={"HIDDEN"})
    delivery_ready: BoolProperty(default=False, options={"HIDDEN"})
    delivery_summary: StringProperty(default="", options={"HIDDEN"})
    delivery_metrics_summary: StringProperty(default="", options={"HIDDEN"})
    delivery_result_path: StringProperty(default="", options={"HIDDEN"})
    terrain_created: BoolProperty(default=False, options={"HIDDEN"})
    import_id: StringProperty(default="", options={"HIDDEN"})
    pack_imagery: BoolProperty(
        name="Pack PNOA into .blend",
        description="Embed PNOA images in the blend file when creating the terrain",
        default=False,
    )
    full_resolution_mesh: BoolProperty(
        name="Full-Resolution Base Mesh",
        description=(
            "Create one base vertex per elevation sample; this can use substantially more "
            "memory than the progressive mesh"
        ),
        default=False,
    )
    adjust_viewport_clip_end: BoolProperty(
        name="Adjust Viewport Clip End",
        description="Increase viewport clipping distance to fit the created terrain",
        default=True,
    )
    imagery_packed: BoolProperty(default=False, options={"HIDDEN"})
    imagery_available: BoolProperty(default=False, options={"HIDDEN"})
    imagery_size_mib: FloatProperty(default=0.0, min=0.0, options={"HIDDEN"})
    active_import_id: EnumProperty(
        name="Terrain Import",
        items=_terrain_import_items,
        update=_active_import_changed,
    )
    active_import_representation: StringProperty(default="", options={"HIDDEN"})
    active_import_full_resolution_mesh: BoolProperty(default=False, options={"HIDDEN"})
    import_settings_tab: EnumProperty(
        name="Terrain Settings",
        items=(
            ("WHOLE", "Whole Import", "Edit every object in the terrain import"),
            ("SELECTED", "Selected Objects", "Edit only selected terrain objects"),
        ),
        default="WHOLE",
        update=_import_settings_tab_changed,
    )
    terrain_vertical_scale: FloatProperty(
        name="Vertical Scale", default=1.0, min=0.001, max=100.0
    )
    terrain_strength_multiplier: FloatProperty(
        name="Strength Multiplier", default=1.0, min=0.0, max=10.0
    )
    terrain_displacement_midlevel: FloatProperty(
        name="Midlevel",
        description="Texture value treated as zero displacement; 0 preserves source elevations",
        default=0.0,
        min=0.0,
        max=1.0,
    )
    terrain_subdivision_viewport: IntProperty(
        name="Viewport Subdivision",
        description="Subdivision shown in the viewport; cost grows fourfold per level",
        default=0,
        min=0,
        max=11,
    )
    terrain_subdivision_render: IntProperty(
        name="Render Subdivision",
        description="Subdivision used for rendering; high levels may exhaust memory",
        default=0,
        min=0,
        max=11,
    )
    terrain_displacement_enabled: BoolProperty(name="Enable Displacement", default=True)
    selected_strength_multiplier: FloatProperty(
        name="Strength Multiplier", default=1.0, min=0.0, max=10.0
    )
    selected_displacement_midlevel: FloatProperty(
        name="Midlevel",
        description="Midlevel for the selected terrain objects",
        default=0.0,
        min=0.0,
        max=1.0,
    )
    selected_object_name: StringProperty(default="", options={"HIDDEN"})
    selected_objects_signature: StringProperty(default="", options={"HIDDEN"})
    selected_subdivision_viewport: IntProperty(
        name="Selected Viewport",
        description="Viewport subdivision for selected terrain objects",
        default=0,
        min=0,
        max=11,
    )
    selected_subdivision_render: IntProperty(
        name="Selected Render",
        description="Render subdivision for selected terrain objects",
        default=0,
        min=0,
        max=11,
    )
