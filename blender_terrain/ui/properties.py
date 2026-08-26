"""Scene properties used to enter and validate a rectangular ROI."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

_IMPORT_ITEMS_CACHE: list[tuple[str, str, str]] = []


def _terrain_import_items(
    properties: object, context: bpy.types.Context
) -> list[tuple[str, str, str]]:
    from .terrain_controls import import_items

    _IMPORT_ITEMS_CACHE[:] = import_items()
    return _IMPORT_ITEMS_CACHE


def _active_import_changed(properties: object, context: bpy.types.Context) -> None:
    from .terrain_controls import load_import_settings

    load_import_settings(properties)


def _invalidate_validation(properties: object, context: bpy.types.Context) -> None:
    """Mark estimates stale whenever an input option changes."""

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


class BLENDERTERRAIN_ROIProperties(bpy.types.PropertyGroup):
    """Store manual WGS84 bounds and their latest validation result."""

    roi_input_mode: EnumProperty(
        name="ROI Input",
        items=(
            ("BOUNDING_BOX", "Bounding Box", "Enter WGS84 rectangle coordinates"),
            ("CENTER_SIZE", "Center + Size", "Enter a WGS84 centre and metric dimensions"),
        ),
        default="BOUNDING_BOX",
        update=_invalidate_validation,
    )
    west: FloatProperty(
        name="West", default=-0.39, min=-180.0, max=180.0, precision=6,
        update=_invalidate_validation,
    )
    south: FloatProperty(
        name="South", default=39.46, min=-90.0, max=90.0, precision=6,
        update=_invalidate_validation,
    )
    east: FloatProperty(
        name="East", default=-0.37, min=-180.0, max=180.0, precision=6,
        update=_invalidate_validation,
    )
    north: FloatProperty(
        name="North", default=39.48, min=-90.0, max=90.0, precision=6,
        update=_invalidate_validation,
    )
    center_longitude: FloatProperty(
        name="Longitude", default=-0.38, min=-180.0, max=180.0, precision=6,
        update=_invalidate_validation,
    )
    center_latitude: FloatProperty(
        name="Latitude", default=39.47, min=-90.0, max=90.0, precision=6,
        update=_invalidate_validation,
    )
    roi_width_metres: FloatProperty(
        name="Width (m)", default=2_000.0, min=1.0, max=1_000_000.0,
        update=_invalidate_validation,
    )
    roi_height_metres: FloatProperty(
        name="Height (m)", default=2_000.0, min=1.0, max=1_000_000.0,
        update=_invalidate_validation,
    )
    product: EnumProperty(
        name="Elevation Product",
        items=(("MDT02", "DTM (MDT02)", "Bare-earth terrain"),
               ("MDS02", "DSM (MDS02)", "Terrain, buildings and vegetation")),
        default="MDT02",
        update=_invalidate_validation,
    )
    elevation_resolution: EnumProperty(
        name="Elevation Resolution",
        items=(
            ("AUTO", "Auto", "Choose the finest safe resolution"),
            *tuple(
                (str(value), f"{value} m", "Output grid spacing")
                for value in (2, 5, 10, 20, 50, 100)
            ),
        ),
        default="AUTO",
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
    terrain_tile_count: IntProperty(default=0, min=0, options={"HIDDEN"})
    terrain_tile_summary: StringProperty(default="", options={"HIDDEN"})
    estimated_memory_mib: FloatProperty(default=0.0, min=0.0, options={"HIDDEN"})
    planning_warning: StringProperty(default="", options={"HIDDEN"})
    job_active: BoolProperty(default=False, options={"HIDDEN"})
    active_job_mode: StringProperty(default="", options={"HIDDEN"})
    job_state: StringProperty(default="", options={"HIDDEN"})
    job_progress: FloatProperty(
        default=0.0, min=0.0, max=1.0, subtype="FACTOR", options={"HIDDEN"}
    )
    job_message: StringProperty(default="", options={"HIDDEN"})
    discovered_file_count: IntProperty(default=0, min=0, options={"HIDDEN"})
    estimated_download_mb: FloatProperty(default=0.0, min=0.0, options={"HIDDEN"})
    discovery_summary: StringProperty(default="", options={"HIDDEN"})
    discovery_ready: BoolProperty(default=False, options={"HIDDEN"})
    delivery_ready: BoolProperty(default=False, options={"HIDDEN"})
    delivery_summary: StringProperty(default="", options={"HIDDEN"})
    delivery_result_path: StringProperty(default="", options={"HIDDEN"})
    vertical_scale: FloatProperty(name="Vertical Scale", default=1.0, min=0.001, max=100.0)
    terrain_created: BoolProperty(default=False, options={"HIDDEN"})
    import_id: StringProperty(default="", options={"HIDDEN"})
    pack_imagery: BoolProperty(
        name="Pack PNOA into .blend",
        description="Embed PNOA images in the blend file when creating the terrain",
        default=False,
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
    terrain_vertical_scale: FloatProperty(
        name="Vertical Scale", default=1.0, min=0.001, max=100.0
    )
    terrain_subdivision_viewport: IntProperty(
        name="Viewport Subdivision", default=0, min=0, max=6
    )
    terrain_subdivision_render: IntProperty(
        name="Render Subdivision", default=0, min=0, max=8
    )
    terrain_displacement_enabled: BoolProperty(name="Enable Displacement", default=True)
    selected_strength_multiplier: FloatProperty(
        name="Strength Multiplier", default=1.0, min=0.0, max=10.0
    )
    selected_subdivision_viewport: IntProperty(
        name="Selected Viewport", default=0, min=0, max=6
    )
    selected_subdivision_render: IntProperty(
        name="Selected Render", default=0, min=0, max=8
    )
