"""Collapsible workflow panels for the BlenderTerrain extension."""

from __future__ import annotations

import json

import bpy

from .terrain_controls import (
    has_selected_terrain_objects,
    import_native_subdivision_level,
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
        properties = context.scene.blender_terrain_roi
        sections = (
            (
                "show_acquisition_section",
                "1. Data Acquisition",
                "WORLD_DATA" if properties.elevation_source == "CNIG" else "FILE_FOLDER",
                _draw_data_section,
            ),
            (
                "show_creation_section",
                "2. Terrain Creation",
                "MESH_GRID",
                _draw_creation_section,
            ),
            (
                "show_imported_section",
                "3. Imported Terrain",
                "MOD_DISPLACE",
                _draw_imported_section,
            ),
            ("show_cache_section", "4. Cache", "DISK_DRIVE", _draw_cache_section),
        )
        for property_name, label, icon, draw_section in sections:
            header, body = self.layout.panel_prop(properties, property_name)
            header.label(text=label, icon=icon)
            if body is not None:
                draw_section(body, context)


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
    inputs.operator(
        "blender_terrain.validate_roi",
        text="ROI Validated" if properties.is_valid else "Validate ROI",
        icon="CHECKMARK" if properties.is_valid else "FILE_TICK",
    )
    layout.separator()
    if properties.is_valid:
        summary = layout.row(align=True)
        summary.scale_y = 0.8
        summary.label(text=properties.crs_summary)
        summary.label(text=f"{properties.area_square_metres / 1_000_000:.2f} km²")
        summary.label(text=f"{properties.estimated_memory_mib:.1f} MiB+")


def _draw_data_section(layout: bpy.types.UILayout, context: bpy.types.Context) -> None:
    properties = context.scene.blender_terrain_roi
    settings = layout.column()
    settings.enabled = not properties.job_active
    settings.row().prop(properties, "elevation_source", expand=True)
    settings.separator()
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
        cnig_product = properties.product not in {
            "COPERNICUS_GLO30_2021",
            "GEDTM30_V11",
            "FR_RGE_ALTI_1M",
            "FR_MNS_CORREL_50CM",
        }
        if products_loaded and cnig_product:
            layout.operator("blender_terrain.check_product_availability", icon="WORLD_DATA")
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
        layout.prop(properties, "imagery_product")
        if properties.imagery_product in {"PNOA_MA", "FR_BD_ORTHO"}:
            layout.prop(properties, "imagery_gsd", text="Resolution")
    if properties.is_valid:
        details = layout.box()
        details.label(text="Selected Data", icon="INFO")
        row = details.row(align=True)
        row.scale_y = 0.8
        row.label(text=f"Elevation: {_elevation_product_label(properties.product)}")
        resolution_label = (
            f"{properties.selected_resolution:g} m (Auto)"
            if properties.elevation_resolution == "AUTO"
            else f"{properties.selected_resolution:g} m"
        )
        row.label(text=resolution_label)
        row = details.row(align=True)
        row.scale_y = 0.8
        row.label(text=f"Samples: {properties.sample_count:,}")
        row.label(text=f"Vertices: {properties.estimated_base_vertices:,}")
        row = details.row(align=True)
        row.scale_y = 0.8
        row.label(text=f"Objects: {properties.terrain_tile_count}")
        row.label(text=properties.terrain_tile_summary)
        row = details.row(align=True)
        row.scale_y = 0.8
        row.label(text=f"Imagery: {properties.imagery_summary}")
        if properties.imagery_product != "NONE":
            row.label(text=f"GPU {properties.estimated_texture_gpu_mib:.1f} MiB")
        details.label(
            text=(
                "Marine: GEBCO seabed"
                if properties.bathymetry_mode == "GEBCO"
                else "Marine: flat water level"
            )
        )
        for warning in properties.planning_warning.split(" | "):
            if warning:
                details.label(text=warning, icon="INFO")


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
    layout.prop(properties, "tiling_mode")
    if properties.tiling_mode == "MANUAL":
        row = layout.row(align=True)
        row.prop(properties, "manual_tile_rows")
        row.prop(properties, "manual_tile_columns")
    if properties.elevation_source == "CNIG":
        layout.prop(properties, "bathymetry_mode")


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
            else "Discover Sources to Check Data Availability"
        ),
        icon="VIEWZOOM",
    )
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
    for message, progress in _job_history(properties.job_event_history)[-5:]:
        row = activity.row()
        row.scale_y = 0.8
        prefix = "" if progress is None else f"{progress:.0%} · "
        row.label(text=f"{prefix}{message}", icon="DOT")
    summary = properties.delivery_summary or properties.discovery_summary
    if summary:
        if properties.delivery_summary and properties.imagery_size_mib:
            summary += f"; imagery {properties.imagery_size_mib:.1f} MiB"
        activity.label(text=summary, icon="FILE_TICK")
    if properties.delivery_metrics_summary:
        activity.label(text=properties.delivery_metrics_summary, icon="TIME")
    if properties.job_active:
        activity.operator("blender_terrain.cancel_discovery", icon="CANCEL")
    elif properties.last_job_path:
        activity.operator("blender_terrain.retry_job", icon="FILE_REFRESH")


def _draw_creation_section(layout: bpy.types.UILayout, context: bpy.types.Context) -> None:
    properties = context.scene.blender_terrain_roi
    if not properties.delivery_ready:
        layout.label(text="Download and process data first", icon="INFO")
        return
    controls = layout.column()
    controls.enabled = not properties.job_active
    controls.prop(properties, "full_resolution_mesh")
    if properties.full_resolution_mesh:
        controls.label(
            text=f"Up to {properties.sample_count:,} base elevation samples",
            icon="ERROR",
        )
        controls.label(text="May use substantial RAM and make Blender unresponsive", icon="ERROR")
    if properties.imagery_available:
        controls.prop(
            properties,
            "pack_imagery",
            text=f"Pack external imagery into .blend ({properties.imagery_size_mib:.1f} MiB)",
        )
    controls.prop(properties, "adjust_viewport_clip_end")
    controls.operator("blender_terrain.create_terrain", icon="MESH_GRID")
    controls.operator("blender_terrain.export_prepared_rasters", icon="EXPORT")


def _draw_cache_section(layout: bpy.types.UILayout, context: bpy.types.Context) -> None:
    properties = context.scene.blender_terrain_roi
    layout.label(text=properties.cache_inventory_summary, icon="DISK_DRIVE")
    layout.operator("blender_terrain.refresh_cache", icon="FILE_REFRESH")
    for name, files, bytes_, partials in _cache_entries(properties.cache_inventory_json):
        suffix = f", {partials} incomplete" if partials else ""
        layout.label(text=f"{name.title()}: {files} file(s), {_format_bytes(bytes_)}{suffix}")
    controls = layout.column()
    controls.enabled = not properties.job_active
    controls.prop(properties, "cache_cleanup_selection")
    controls.operator("blender_terrain.clear_cache", icon="TRASH")


def _draw_imported_section(layout: bpy.types.UILayout, context: bpy.types.Context) -> None:
    properties = context.scene.blender_terrain_roi
    controls = layout
    if not any(
        isinstance(collection.get("blender_terrain_import_id"), str)
        for collection in bpy.data.collections
    ):
        controls.label(text="No terrain has been created yet", icon="INFO")
        return
    controls.prop(properties, "active_import_id")
    controls.operator("blender_terrain.select_import_objects", icon="RESTRICT_SELECT_OFF")
    if properties.active_import_representation == "BAKED":
        controls.label(text="Baked terrain: displacement controls unavailable", icon="INFO")
        return
    editable = controls.column()
    editable.enabled = properties.active_import_representation == "DISPLACEMENT"
    editable.row().prop(properties, "import_settings_tab", expand=True)
    native_level = import_native_subdivision_level(properties.active_import_id)
    viewport_label = f"Viewport Subdivision (Source Grid >= {native_level})"
    render_label = f"Render Subdivision (Source Grid >= {native_level})"
    if properties.import_settings_tab == "WHOLE":
        editable.prop(properties, "terrain_vertical_scale")
        editable.prop(properties, "terrain_strength_multiplier")
        editable.prop(properties, "terrain_displacement_midlevel")
        editable.prop(properties, "terrain_subdivision_viewport", text=viewport_label)
        editable.prop(properties, "terrain_subdivision_render", text=render_label)
        editable.prop(properties, "terrain_displacement_enabled")
        editable.prop(properties, "terrain_smooth_angle")
        editable.operator("blender_terrain.apply_import_settings")
    else:
        has_selection = has_selected_terrain_objects(context, properties.active_import_id)
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
        selected.prop(properties, "selected_subdivision_viewport", text=viewport_label)
        selected.prop(properties, "selected_subdivision_render", text=render_label)
        selected.prop(properties, "selected_smooth_angle")
        row = selected.row(align=True)
        row.operator("blender_terrain.apply_selected_settings")
        row.operator("blender_terrain.restore_selected_settings")
    editable.separator()
    editable.operator("blender_terrain.bake_and_merge_terrain", icon="MODIFIER")


def _job_state_label(state: str) -> str:
    return {
        "VALIDATING": "Validating",
        "DISCOVERING": "Finding sources",
        "DOWNLOADING_ELEVATION": "Downloading elevation",
        "DOWNLOADING_IMAGERY": "Downloading imagery",
        "PROCESSING_ELEVATION": "Processing elevation",
    }.get(state, state.replace("_", " ").title())


def _elevation_product_label(product_id: str) -> str:
    return {
        "COPERNICUS_GLO30_2021": "Copernicus GLO-30 DSM",
        "GEDTM30_V11": "GEDTM30 v1.1 modelled DTM",
        "FR_RGE_ALTI_1M": "RGE ALTI 1 m DTM",
        "FR_MNS_CORREL_50CM": "MNS-Correl 50 cm DSM",
    }.get(product_id, product_id)


def _job_history(serialized: str) -> tuple[tuple[str, float | None], ...]:
    try:
        values = json.loads(serialized)
    except json.JSONDecodeError:
        return ()
    if not isinstance(values, list):
        return ()
    history: list[tuple[str, float | None]] = []
    for value in values:
        if isinstance(value, str):
            history.append((value, None))
        elif isinstance(value, dict) and isinstance(value.get("message"), str):
            progress = value.get("progress")
            history.append(
                (
                    value["message"],
                    float(progress) if isinstance(progress, (int, float)) else None,
                )
            )
    return tuple(history)


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
        if isinstance(product, str) and isinstance(status, str) and isinstance(file_count, int):
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
        if isinstance(fields[0], str) and all(isinstance(field, int) for field in fields[1:]):
            entries.append((fields[0], fields[1], fields[2], fields[3]))
    return tuple(entries)


def _format_bytes(byte_count: int) -> str:
    value = float(byte_count)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    raise AssertionError("Unreachable byte unit")
