"""Collapsible workflow panels for the BlenderTerrain extension."""

from __future__ import annotations

import json

import bpy

from ..core import SUBDIVISION_WARNING_LEVEL
from .terrain_controls import (
    has_selected_terrain_objects,
    request_selected_settings_sync,
)


class BLENDERTERRAIN_PT_main(bpy.types.Panel):
    """Own the BlenderTerrain sidebar hierarchy."""

    bl_idname = "BLENDERTERRAIN_PT_main"
    bl_label = "BlenderTerrain"
    bl_category = "Terrain"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context: bpy.types.Context) -> None:
        pass


class BLENDERTERRAIN_PT_source(bpy.types.Panel):
    """Choose the workflow before presenting source-specific controls."""

    bl_idname = "BLENDERTERRAIN_PT_source"
    bl_label = "1. Data Source"
    bl_parent_id = "BLENDERTERRAIN_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw_header(self, context: bpy.types.Context) -> None:
        icon = (
            "WORLD_DATA"
            if context.scene.blender_terrain_roi.elevation_source == "CNIG"
            else "FILE_FOLDER"
        )
        self.layout.label(text="", icon=icon)

    def draw(self, context: bpy.types.Context) -> None:
        properties = context.scene.blender_terrain_roi
        controls = self.layout.column()
        controls.enabled = not properties.job_active
        controls.prop(properties, "elevation_source", expand=True)
        if properties.elevation_source == "CNIG":
            controls.label(text="Area, products and downloads are configured below", icon="INFO")
        else:
            controls.label(
                text="Elevation is required; local imagery is not yet available",
                icon="INFO",
            )


def _draw_area_settings(layout: bpy.types.UILayout, context: bpy.types.Context) -> None:
    properties = context.scene.blender_terrain_roi
    inputs = layout.column()
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
    layout.separator()
    layout.label(
        text=properties.validation_message,
        icon="CHECKMARK" if properties.is_valid else "INFO",
    )
    if properties.is_valid:
        layout.label(text=properties.crs_summary)
        layout.label(
            text=f"Area: {properties.area_square_metres / 1_000_000:.3f} km^2"
        )
        layout.label(
            text=f"Estimated memory: {properties.estimated_memory_mib:.1f} MiB+"
        )


class BLENDERTERRAIN_PT_data(bpy.types.Panel):
    """Configure, discover and prepare online or local data."""

    bl_idname = "BLENDERTERRAIN_PT_data"
    bl_label = "2. Data Acquisition"
    bl_parent_id = "BLENDERTERRAIN_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context: bpy.types.Context) -> None:
        properties = context.scene.blender_terrain_roi
        layout = self.layout
        settings = layout.column()
        settings.enabled = not properties.job_active
        if properties.elevation_source == "LOCAL":
            _draw_local_settings(settings, properties)
        else:
            area = settings.box()
            area.label(text="Area of Interest", icon="WORLD_DATA")
            _draw_area_settings(area, context)
            settings.separator()
            _draw_online_settings(settings, properties)
        layout.separator()
        _draw_acquisition_controls(layout, properties)
        layout.separator()
        _draw_job_activity(layout, properties)


def _draw_online_settings(layout: bpy.types.UILayout, properties: object) -> None:
    layout.row().prop(properties, "data_settings_tab", expand=True)
    if properties.data_settings_tab == "ELEVATION":
        products_loaded = properties.available_product_ids_json != "[]"
        if products_loaded:
            layout.prop(properties, "product")
        else:
            layout.label(text="Validate the ROI to load available products", icon="INFO")
        if products_loaded and properties.product != "COPERNICUS_GLO30_2021":
            layout.operator("blender_terrain.check_product_availability", icon="WORLD_DATA")
        elif products_loaded:
            layout.label(text="Global 30 m surface model; not a bare-earth DTM", icon="INFO")
        if properties.product_availability_summary:
            availability_box = layout.box()
            availability_box.label(text=properties.product_availability_summary, icon="INFO")
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
        _draw_elevation_output_settings(layout, properties)
    else:
        if properties.product == "COPERNICUS_GLO30_2021":
            layout.label(text="Global imagery is not implemented yet", icon="INFO")
            return
        layout.prop(properties, "use_imagery")
        if properties.use_imagery:
            layout.prop(properties, "imagery_gsd", text="Resolution")
            layout.label(text="GSD is ground metres represented by each pixel", icon="INFO")
        if properties.is_valid:
            layout.label(text=properties.imagery_summary)
            layout.label(
                text=(
                    "Estimated decoded texture: "
                    f"{properties.estimated_texture_gpu_mib:.1f} MiB"
                )
            )
            if properties.use_imagery and properties.imagery_gsd == "AUTO":
                layout.label(text="The value above is the resolved Auto GSD", icon="CHECKMARK")
    if properties.is_valid and properties.planning_warning:
        layout.label(text=properties.planning_warning, icon="INFO")


def _draw_local_settings(layout: bpy.types.UILayout, properties: object) -> None:
    elevation = layout.box()
    elevation.label(text="Elevation Raster (Required)", icon="IMAGE_DATA")
    elevation.prop(properties, "local_elevation_path", text="File or Folder")
    elevation.label(text="Compatible CNIG Float32 TIFF or TIFF folder", icon="INFO")
    if properties.local_elevation_summary:
        elevation.label(text=properties.local_elevation_summary, icon="CHECKMARK")
    elevation.prop(properties, "product", text="Elevation Model")
    _draw_elevation_output_settings(elevation, properties)
    imagery = layout.box()
    imagery.label(text="Local Imagery (Optional)", icon="IMAGE_RGB")
    imagery.prop(properties, "use_local_imagery")
    if properties.use_local_imagery:
        imagery.prop(properties, "local_imagery_path")
        imagery.label(text="PNG + PGW/WLD + PRJ", icon="INFO")
        if properties.local_imagery_summary:
            imagery.label(text=properties.local_imagery_summary, icon="CHECKMARK")
    else:
        imagery.label(text="Blender's default material will be used", icon="INFO")


def _draw_elevation_output_settings(layout: bpy.types.UILayout, properties: object) -> None:
    layout.prop(properties, "elevation_resolution")
    layout.prop(properties, "resource_profile")
    if properties.resource_profile == "LARGE":
        layout.label(text="Higher limits may freeze Blender", icon="ERROR")
    layout.prop(properties, "tiling_mode", text="Terrain Object Grid")
    layout.label(text="Elevation and imagery use the same object grid", icon="INFO")
    if properties.tiling_mode == "MANUAL":
        row = layout.row(align=True)
        row.prop(properties, "manual_tile_rows")
        row.prop(properties, "manual_tile_columns")
    if properties.is_valid:
        prefix = "Auto resolved to" if properties.elevation_resolution == "AUTO" else "Output"
        layout.label(text=f"{prefix}: {properties.selected_resolution:g} m")
        layout.label(text=f"Elevation samples: {properties.sample_count:,}")
        layout.label(text=f"Full-resolution vertices: {properties.estimated_base_vertices:,}")
        layout.label(text=f"Terrain objects: {properties.terrain_tile_count}")
        layout.label(text=properties.terrain_tile_summary)


def _draw_acquisition_controls(layout: bpy.types.UILayout, properties: object) -> None:
    controls = layout.column()
    controls.enabled = not properties.job_active
    discover = controls.row()
    discover.enabled = bpy.app.online_access or properties.elevation_source == "LOCAL"
    discover.operator(
        "blender_terrain.discover_sources",
        text=(
            "Inspect Local Sources"
            if properties.elevation_source == "LOCAL"
            else "Discover Sources"
        ),
        icon="VIEWZOOM",
    )
    if properties.elevation_source == "CNIG":
        controls.label(text="Discovery checks coverage and size without downloading", icon="INFO")
    prepare = controls.row()
    prepare.enabled = properties.discovery_ready and (
        bpy.app.online_access or properties.elevation_source == "LOCAL"
    )
    prepare.operator(
        "blender_terrain.download_data",
        text=(
            "Prepare Local Data"
            if properties.elevation_source == "LOCAL"
            else "Download and Prepare"
        ),
        icon="IMPORT",
    )
    if not bpy.app.online_access and properties.elevation_source == "CNIG":
        layout.label(text="Online access is disabled in Preferences", icon="ERROR")
    if properties.discovery_summary:
        layout.label(text=properties.discovery_summary, icon="FILE_TICK")
    if properties.delivery_summary:
        layout.label(text=properties.delivery_summary, icon="CHECKMARK")
    if properties.delivery_metrics_summary:
        layout.label(text=properties.delivery_metrics_summary, icon="TIME")


def _draw_job_activity(layout: bpy.types.UILayout, properties: object) -> None:
    activity = layout.box()
    activity.label(text="Activity", icon="TIME")
    if properties.job_state:
        activity.progress(
            factor=properties.job_progress,
            type="BAR",
            text=f"{_job_state_label(properties.job_state)}: {properties.job_progress:.0%}",
        )
    else:
        activity.label(text="No task has been started")
    for message in _job_history(properties.job_event_history)[-5:]:
        row = activity.row()
        row.scale_y = 0.8
        row.label(text=message, icon="DOT")
    if properties.job_active:
        activity.operator("blender_terrain.cancel_discovery", icon="CANCEL")
    elif properties.last_job_path:
        activity.operator("blender_terrain.retry_job", icon="FILE_REFRESH")


class BLENDERTERRAIN_PT_creation(bpy.types.Panel):
    """Create Blender objects from a completed data delivery."""

    bl_idname = "BLENDERTERRAIN_PT_creation"
    bl_label = "3. Terrain Creation"
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
                text=f"External imagery size: {properties.imagery_size_mib:.1f} MiB",
                icon="INFO",
            )
        controls.prop(properties, "adjust_viewport_clip_end")
        controls.operator("blender_terrain.create_terrain", icon="MESH_GRID")
        if properties.terrain_created and properties.imagery_available:
            if properties.imagery_packed:
                self.layout.label(text="Terrain images packed in .blend", icon="PACKAGE")
            else:
                self.layout.operator("blender_terrain.pack_imagery", icon="PACKAGE")


class BLENDERTERRAIN_PT_cache(bpy.types.Panel):
    """Inspect and selectively maintain regenerable cache data."""

    bl_idname = "BLENDERTERRAIN_PT_cache"
    bl_label = "5. Cache"
    bl_parent_id = "BLENDERTERRAIN_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context: bpy.types.Context) -> None:
        properties = context.scene.blender_terrain_roi
        layout = self.layout
        layout.label(text=properties.cache_inventory_summary, icon="DISK_DRIVE")
        layout.operator("blender_terrain.refresh_cache", icon="FILE_REFRESH")
        for name, files, bytes_, partials in _cache_entries(properties.cache_inventory_json):
            suffix = f", {partials} incomplete" if partials else ""
            layout.label(
                text=f"{name.title()}: {files} file(s), {_format_bytes(bytes_)}{suffix}"
            )
        controls = layout.column()
        controls.enabled = not properties.job_active
        controls.prop(properties, "cache_cleanup_selection")
        controls.operator("blender_terrain.clear_cache", icon="TRASH")


class BLENDERTERRAIN_PT_imported(bpy.types.Panel):
    """Edit displacement settings on existing terrain imports."""

    bl_idname = "BLENDERTERRAIN_PT_imported"
    bl_label = "4. Imported Terrain"
    bl_parent_id = "BLENDERTERRAIN_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context: bpy.types.Context) -> None:
        properties = context.scene.blender_terrain_roi
        controls = self.layout
        if not any(
            isinstance(collection.get("blender_terrain_import_id"), str)
            for collection in bpy.data.collections
        ):
            controls.label(text="No terrain has been created yet", icon="INFO")
            return
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
            has_selection = has_selected_terrain_objects(
                context, properties.active_import_id
            )
            request_selected_settings_sync()
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


def _cache_entries(serialized: str) -> tuple[tuple[str, int, int, int], ...]:
    try:
        values = json.loads(serialized)
    except json.JSONDecodeError:
        return ()
    if not isinstance(values, list):
        return ()
    entries: list[tuple[str, int, int, int]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        fields = (
            value.get("name"),
            value.get("files"),
            value.get("bytes"),
            value.get("partials"),
        )
        if isinstance(fields[0], str) and all(
            isinstance(field, int) for field in fields[1:]
        ):
            entries.append((fields[0], fields[1], fields[2], fields[3]))
    return tuple(entries)


def _format_bytes(byte_count: int) -> str:
    value = float(byte_count)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    raise AssertionError("Unreachable byte unit")


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
