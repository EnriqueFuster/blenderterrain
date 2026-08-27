"""Collapsible workflow panels for the BlenderTerrain extension."""

from __future__ import annotations

import json

import bpy

from ..core import SUBDIVISION_WARNING_LEVEL
from .terrain_controls import sync_selected_settings


class BLENDERTERRAIN_PT_main(bpy.types.Panel):
    """Own the BlenderTerrain sidebar hierarchy."""

    bl_idname = "BLENDERTERRAIN_PT_main"
    bl_label = "BlenderTerrain"
    bl_category = "Terrain"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context: bpy.types.Context) -> None:
        properties = context.scene.blender_terrain_roi
        if properties.job_active:
            self.layout.label(text=_job_state_label(properties.job_state), icon="TIME")
        elif properties.terrain_created:
            self.layout.label(text="Terrain ready", icon="CHECKMARK")
        else:
            self.layout.label(text="Define an area to begin", icon="WORLD_DATA")


class BLENDERTERRAIN_PT_area(bpy.types.Panel):
    """Collect and validate the area of interest."""

    bl_idname = "BLENDERTERRAIN_PT_area"
    bl_label = "Area of Interest"
    bl_parent_id = "BLENDERTERRAIN_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context: bpy.types.Context) -> None:
        properties = context.scene.blender_terrain_roi
        inputs = self.layout.column()
        inputs.enabled = not properties.job_active
        inputs.prop(properties, "roi_input_mode")
        if properties.roi_input_mode == "CENTER_SIZE":
            row = inputs.row(align=True)
            row.prop(properties, "center_longitude")
            row.prop(properties, "center_latitude")
            row = inputs.row(align=True)
            row.prop(properties, "roi_width_metres")
            row.prop(properties, "roi_height_metres")
            inputs.operator("blender_terrain.update_bbox_from_center", icon="FILE_REFRESH")
            inputs.label(
                text=(
                    f"BBox: {properties.west:.6f}, {properties.south:.6f}, "
                    f"{properties.east:.6f}, {properties.north:.6f}"
                )
            )
        elif properties.roi_input_mode == "BOUNDING_BOX":
            row = inputs.row(align=True)
            row.prop(properties, "west")
            row.prop(properties, "east")
            row = inputs.row(align=True)
            row.prop(properties, "south")
            row.prop(properties, "north")
        elif properties.roi_input_mode == "FILE":
            inputs.prop(properties, "roi_file_path")
            if properties.roi_file_path.lower().endswith(".gpkg"):
                inputs.prop(properties, "gpkg_layer")
                if properties.gpkg_inspection_message:
                    inputs.label(text=properties.gpkg_inspection_message, icon="INFO")
            else:
                inputs.label(text="GeoJSON, KML, SHP + PRJ, or GPKG", icon="INFO")
        else:
            inputs.operator("blender_terrain.open_roi_map", icon="URL")
            if properties.roi_geometry_json:
                inputs.label(
                    text=(
                        f"Selected: {properties.west:.6f}, {properties.south:.6f}, "
                        f"{properties.east:.6f}, {properties.north:.6f}"
                    ),
                    icon="CHECKMARK",
                )
            else:
                inputs.label(text="The selector opens in your browser", icon="INFO")
        if properties.roi_input_mode != "FILE":
            row = inputs.row(align=True)
            row.operator("blender_terrain.copy_bbox", icon="COPYDOWN")
            row.operator("blender_terrain.paste_bbox", icon="PASTEDOWN")
        inputs.operator("blender_terrain.validate_roi", icon="CHECKMARK")
        self.layout.separator()
        self.layout.label(
            text=properties.validation_message,
            icon="CHECKMARK" if properties.is_valid else "INFO",
        )
        if properties.is_valid:
            self.layout.label(text=properties.crs_summary)
            self.layout.label(
                text=f"Area: {properties.area_square_metres / 1_000_000:.3f} km²"
            )
            self.layout.label(
                text=f"Estimated memory: {properties.estimated_memory_mib:.1f} MiB+"
            )


class BLENDERTERRAIN_PT_data(bpy.types.Panel):
    """Configure elevation and imagery output."""

    bl_idname = "BLENDERTERRAIN_PT_data"
    bl_label = "Data Settings"
    bl_parent_id = "BLENDERTERRAIN_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context: bpy.types.Context) -> None:
        properties = context.scene.blender_terrain_roi
        layout = self.layout
        layout.enabled = not properties.job_active
        layout.row().prop(properties, "data_settings_tab", expand=True)
        if properties.data_settings_tab == "ELEVATION":
            layout.prop(properties, "elevation_source")
            if properties.elevation_source == "LOCAL":
                layout.prop(properties, "local_elevation_path")
                layout.label(text="Supports the verified CNIG elevation TIFF layout", icon="INFO")
            layout.prop(properties, "product")
            if properties.elevation_source == "CNIG":
                layout.operator(
                    "blender_terrain.check_product_availability", icon="WORLD_DATA"
                )
                if properties.product_availability_summary:
                    availability_box = layout.box()
                    availability_box.label(
                        text=properties.product_availability_summary, icon="INFO"
                    )
                    for product, status, file_count in _availability_entries(
                        properties.product_availability_json
                    ):
                        if status == "AVAILABLE":
                            text = f"{product}: available ({file_count} source file(s))"
                            icon = "CHECKMARK"
                        elif status == "NO_COVERAGE":
                            text = f"{product}: no coverage"
                            icon = "CANCEL"
                        else:
                            text = f"{product}: could not be checked"
                            icon = "QUESTION"
                        availability_box.label(text=text, icon=icon)
            if properties.product in {"MDT50CM", "MDS50CM"}:
                layout.label(text="Third-coverage availability is still incomplete", icon="INFO")
            layout.prop(properties, "elevation_resolution")
            layout.prop(properties, "tiling_mode", text="Terrain Object Grid")
            layout.label(
                text="The object grid keeps elevation and imagery mapping together",
                icon="INFO",
            )
            if properties.tiling_mode == "MANUAL":
                row = layout.row(align=True)
                row.prop(properties, "manual_tile_rows")
                row.prop(properties, "manual_tile_columns")
            if properties.is_valid:
                layout.separator()
                prefix = (
                    "Auto resolved to"
                    if properties.elevation_resolution == "AUTO"
                    else "Output"
                )
                layout.label(text=f"{prefix}: {properties.selected_resolution:g} m")
                layout.label(text=f"Elevation samples: {properties.sample_count:,}")
                layout.label(text=f"Terrain objects: {properties.terrain_tile_count}")
                layout.label(text=properties.terrain_tile_summary)
        else:
            layout.prop(properties, "use_imagery")
            if properties.use_imagery:
                layout.prop(properties, "imagery_gsd", text="Resolution")
                layout.label(
                    text="GSD is ground metres represented by each pixel", icon="INFO"
                )
            if properties.is_valid:
                layout.label(text=properties.imagery_summary)
                if properties.use_imagery and properties.imagery_gsd == "AUTO":
                    layout.label(text="The value above is the resolved Auto GSD", icon="CHECKMARK")
        if properties.is_valid and properties.planning_warning:
            layout.label(text=properties.planning_warning, icon="INFO")


class BLENDERTERRAIN_PT_acquisition(bpy.types.Panel):
    """Discover and download official source data."""

    bl_idname = "BLENDERTERRAIN_PT_acquisition"
    bl_label = "Data Acquisition"
    bl_parent_id = "BLENDERTERRAIN_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context: bpy.types.Context) -> None:
        properties = context.scene.blender_terrain_roi
        layout = self.layout
        controls = layout.column()
        controls.enabled = not properties.job_active
        discover = controls.row()
        discover.enabled = bpy.app.online_access or properties.elevation_source == "LOCAL"
        discover.operator("blender_terrain.discover_sources", icon="VIEWZOOM")
        controls.label(text="Discovery checks coverage and size; it does not download", icon="INFO")
        download = controls.row()
        download.enabled = properties.discovery_ready and (
            bpy.app.online_access
            or (properties.elevation_source == "LOCAL" and not properties.use_imagery)
        )
        download.operator("blender_terrain.download_data", icon="IMPORT")
        if not bpy.app.online_access:
            layout.label(text="Online access is disabled in Preferences", icon="ERROR")
        if properties.discovery_summary:
            layout.label(text=properties.discovery_summary, icon="FILE_TICK")
        if properties.delivery_summary:
            layout.label(text=properties.delivery_summary, icon="CHECKMARK")
        layout.separator()
        activity = layout.box()
        activity.label(text="Job Activity", icon="TIME")
        if properties.job_state:
            activity.progress(
                factor=properties.job_progress,
                type="BAR",
                text=(
                    f"{_job_state_label(properties.job_state)}: "
                    f"{properties.job_progress:.0%}"
                ),
            )
        else:
            activity.label(text="No job has been started")
        for message in _job_history(properties.job_event_history)[-5:]:
            row = activity.row()
            row.scale_y = 0.8
            row.label(text=message, icon="DOT")
        if properties.job_active:
            activity.operator("blender_terrain.cancel_discovery", icon="CANCEL")


class BLENDERTERRAIN_PT_creation(bpy.types.Panel):
    """Create Blender objects from a completed data delivery."""

    bl_idname = "BLENDERTERRAIN_PT_creation"
    bl_label = "Terrain Creation"
    bl_parent_id = "BLENDERTERRAIN_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context: bpy.types.Context) -> None:
        properties = context.scene.blender_terrain_roi
        if not properties.delivery_ready:
            self.layout.label(text="Download and process data first", icon="INFO")
            return
        controls = self.layout.column()
        controls.enabled = not properties.job_active
        controls.prop(properties, "full_resolution_mesh")
        if properties.full_resolution_mesh:
            controls.label(
                text=f"Up to {properties.sample_count:,} base elevation samples",
                icon="ERROR",
            )
            controls.label(
                text="May use substantial RAM and make Blender unresponsive", icon="ERROR"
            )
        else:
            controls.label(
                text="Light base mesh; increase subdivision after import", icon="INFO"
            )
        if properties.imagery_available:
            controls.prop(properties, "pack_imagery")
            controls.label(
                text=f"PNOA cache size: {properties.imagery_size_mib:.1f} MiB",
                icon="INFO",
            )
        controls.prop(properties, "adjust_viewport_clip_end")
        controls.operator("blender_terrain.create_terrain", icon="MESH_GRID")
        if properties.terrain_created and properties.imagery_available:
            if properties.imagery_packed:
                self.layout.label(text="PNOA images packed in .blend", icon="PACKAGE")
            else:
                self.layout.operator("blender_terrain.pack_imagery", icon="PACKAGE")


class BLENDERTERRAIN_PT_imported(bpy.types.Panel):
    """Edit displacement settings on existing terrain imports."""

    bl_idname = "BLENDERTERRAIN_PT_imported"
    bl_label = "Imported Terrain"
    bl_parent_id = "BLENDERTERRAIN_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return any(
            isinstance(collection.get("blender_terrain_import_id"), str)
            for collection in bpy.data.collections
        )

    def draw(self, context: bpy.types.Context) -> None:
        properties = context.scene.blender_terrain_roi
        controls = self.layout
        controls.prop(properties, "active_import_id")
        controls.operator("blender_terrain.select_import_objects", icon="RESTRICT_SELECT_OFF")
        if properties.active_import_representation == "DISPLACEMENT":
            controls.label(
                text=(
                    "Full-resolution base mesh; subdivision interpolates"
                    if properties.active_import_full_resolution_mesh
                    else "Progressive mesh; level 4 approximates the source grid"
                ),
                icon="INFO",
            )
        elif properties.active_import_representation == "BAKED":
            controls.label(text="Legacy baked terrain: controls unavailable", icon="INFO")
            return
        editable = controls.column()
        editable.enabled = properties.active_import_representation == "DISPLACEMENT"
        editable.row().prop(properties, "import_settings_tab", expand=True)
        if properties.import_settings_tab == "WHOLE":
            editable.prop(properties, "terrain_vertical_scale")
            editable.prop(properties, "terrain_strength_multiplier")
            editable.prop(properties, "terrain_displacement_midlevel")
            editable.label(text="Midlevel 0 preserves source elevations", icon="INFO")
            editable.prop(properties, "terrain_subdivision_viewport")
            editable.prop(properties, "terrain_subdivision_render")
            _draw_subdivision_warning(
                editable,
                properties.terrain_subdivision_viewport,
                properties.terrain_subdivision_render,
            )
            editable.prop(properties, "terrain_displacement_enabled")
            editable.operator("blender_terrain.apply_import_settings")
        else:
            has_selection = sync_selected_settings(context, properties)
            if has_selection:
                editable.label(
                    text=f"Values from: {properties.selected_object_name}",
                    icon="OBJECT_DATA",
                )
            else:
                editable.label(text="Select an object from this terrain", icon="INFO")
            selected = editable.column()
            selected.enabled = has_selection
            selected.prop(properties, "selected_strength_multiplier")
            selected.prop(properties, "selected_displacement_midlevel")
            selected.prop(properties, "selected_subdivision_viewport")
            selected.prop(properties, "selected_subdivision_render")
            _draw_subdivision_warning(
                selected,
                properties.selected_subdivision_viewport,
                properties.selected_subdivision_render,
            )
            row = selected.row(align=True)
            row.operator("blender_terrain.apply_selected_settings")
            row.operator("blender_terrain.restore_selected_settings")


def _job_state_label(state: str) -> str:
    return {
        "VALIDATING": "Validating",
        "DISCOVERING": "Finding sources",
        "DOWNLOADING_ELEVATION": "Downloading elevation",
        "DOWNLOADING_IMAGERY": "Downloading PNOA imagery",
        "PROCESSING_ELEVATION": "Processing elevation",
    }.get(state, state.replace("_", " ").title())


def _job_history(serialized: str) -> tuple[str, ...]:
    try:
        values = json.loads(serialized)
    except json.JSONDecodeError:
        return ()
    if not isinstance(values, list):
        return ()
    return tuple(value for value in values if isinstance(value, str))


def _availability_entries(serialized: str) -> tuple[tuple[str, str, int], ...]:
    try:
        values = json.loads(serialized)
    except json.JSONDecodeError:
        return ()
    if not isinstance(values, list):
        return ()
    entries: list[tuple[str, str, int]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        product = value.get("product")
        status = value.get("status")
        file_count = value.get("file_count", 0)
        if (
            isinstance(product, str)
            and isinstance(status, str)
            and isinstance(file_count, int)
        ):
            entries.append((product, status, file_count))
    return tuple(entries)


def _draw_subdivision_warning(layout: bpy.types.UILayout, viewport: int, render: int) -> None:
    highest = max(viewport, render)
    if highest < SUBDIVISION_WARNING_LEVEL:
        return
    layout.label(
        text=f"Level {highest}: up to {4**highest:,} faces per base face",
        icon="ERROR",
    )
    if highest >= 6:
        layout.label(text="May freeze Blender or exhaust memory", icon="ERROR")
