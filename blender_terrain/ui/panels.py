"""Sidebar panels for the BlenderTerrain extension."""

from __future__ import annotations

import bpy

from ..core import SUBDIVISION_WARNING_LEVEL


class BLENDERTERRAIN_PT_main(bpy.types.Panel):
    """Display terrain inputs, estimates and background discovery status."""

    bl_idname = "BLENDERTERRAIN_PT_main"
    bl_label = "BlenderTerrain"
    bl_category = "Terrain"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context: bpy.types.Context) -> None:
        """Draw manual ROI input and its latest discovery state."""

        properties = context.scene.blender_terrain_roi
        box = self.layout.box()
        box.enabled = not properties.job_active
        box.label(text="Area of Interest", icon="WORLD_DATA")
        box.prop(properties, "roi_input_mode")
        if properties.roi_input_mode == "CENTER_SIZE":
            row = box.row(align=True)
            row.prop(properties, "center_longitude")
            row.prop(properties, "center_latitude")
            row = box.row(align=True)
            row.prop(properties, "roi_width_metres")
            row.prop(properties, "roi_height_metres")
            box.operator("blender_terrain.update_bbox_from_center", icon="FILE_REFRESH")
            box.label(
                text=(
                    f"BBox: {properties.west:.6f}, {properties.south:.6f}, "
                    f"{properties.east:.6f}, {properties.north:.6f}"
                )
            )
        else:
            row = box.row(align=True)
            row.prop(properties, "west")
            row.prop(properties, "east")
            row = box.row(align=True)
            row.prop(properties, "south")
            row.prop(properties, "north")
        row = box.row(align=True)
        row.operator("blender_terrain.copy_bbox", icon="COPYDOWN")
        row.operator("blender_terrain.paste_bbox", icon="PASTEDOWN")

        elevation = self.layout.box()
        elevation.enabled = not properties.job_active
        elevation.label(text="Elevation", icon="MOD_DISPLACE")
        elevation.prop(properties, "product")
        elevation.prop(properties, "elevation_resolution")
        elevation.prop(properties, "tiling_mode")
        if properties.tiling_mode == "MANUAL":
            row = elevation.row(align=True)
            row.prop(properties, "manual_tile_rows")
            row.prop(properties, "manual_tile_columns")

        imagery = self.layout.box()
        imagery.enabled = not properties.job_active
        imagery.label(text="Imagery", icon="IMAGE_DATA")
        imagery.prop(properties, "use_imagery")
        if properties.use_imagery:
            imagery.prop(properties, "imagery_gsd")

        actions = self.layout.box()
        actions.label(text="Data Sources", icon="URL")
        if properties.job_active:
            actions.prop(
                properties,
                "job_progress",
                text=_job_state_label(properties.job_state),
                slider=True,
            )
            actions.label(text=properties.job_message, icon="INFO")
            actions.operator("blender_terrain.cancel_discovery", icon="CANCEL")
        else:
            row = actions.row(align=True)
            row.operator("blender_terrain.validate_roi", icon="CHECKMARK")
            discover = row.row(align=True)
            discover.enabled = properties.is_valid and bpy.app.online_access
            discover.operator("blender_terrain.discover_sources", icon="VIEWZOOM")
            if not bpy.app.online_access:
                actions.label(
                    text="Online access is disabled in Blender Preferences", icon="ERROR"
                )
            if properties.discovery_summary:
                actions.label(text=properties.discovery_summary, icon="FILE_TICK")
            if properties.discovery_ready:
                actions.operator("blender_terrain.download_data", icon="IMPORT")
            if properties.delivery_summary:
                actions.label(text=properties.delivery_summary, icon="CHECKMARK")
            if properties.delivery_ready:
                actions.prop(properties, "vertical_scale")
                if properties.imagery_available:
                    actions.prop(properties, "pack_imagery")
                    actions.label(
                        text=f"PNOA cache size: {properties.imagery_size_mib:.1f} MiB",
                        icon="INFO",
                    )
                actions.operator("blender_terrain.create_terrain", icon="MESH_GRID")
            if properties.terrain_created and properties.imagery_available:
                if properties.imagery_packed:
                    actions.label(text="PNOA images packed in .blend", icon="PACKAGE")
                else:
                    actions.operator("blender_terrain.pack_imagery", icon="PACKAGE")
            if properties.job_message and properties.job_state:
                icon = (
                    "CHECKMARK"
                    if properties.job_state == "COMPLETE"
                    else "INFO"
                    if properties.job_state == "COMPLETE_WITH_WARNINGS"
                    else "ERROR"
                )
                actions.label(text=properties.job_message, icon=icon)

        if properties.terrain_created:
            imported = self.layout.box()
            imported.label(text="Current Import", icon="OUTLINER_COLLECTION")
            imported.label(text=f"ID: {properties.import_id[:8]}")
            imported.label(text="Source: IGN-CNIG")
            imported.label(text="Data terms: CNIG provider policy")

        terrain_imports = tuple(
            collection
            for collection in bpy.data.collections
            if isinstance(collection.get("blender_terrain_import_id"), str)
        )
        if terrain_imports:
            controls = self.layout.box()
            controls.label(text="Imported Terrain", icon="MOD_DISPLACE")
            controls.prop(properties, "active_import_id")
            controls.operator("blender_terrain.select_import_objects", icon="RESTRICT_SELECT_OFF")
            controls.label(
                text=f"Representation: {properties.active_import_representation or 'Unknown'}"
            )
            editable = controls.column()
            editable.enabled = properties.active_import_representation == "DISPLACEMENT"
            editable.label(text="Whole Import")
            editable.prop(properties, "terrain_vertical_scale")
            editable.prop(properties, "terrain_subdivision_viewport")
            editable.prop(properties, "terrain_subdivision_render")
            _draw_subdivision_warning(
                editable,
                properties.terrain_subdivision_viewport,
                properties.terrain_subdivision_render,
            )
            editable.prop(properties, "terrain_displacement_enabled")
            editable.operator("blender_terrain.apply_import_settings")
            editable.separator()
            editable.label(text="Selected Objects")
            editable.prop(properties, "selected_strength_multiplier")
            editable.prop(properties, "selected_subdivision_viewport")
            editable.prop(properties, "selected_subdivision_render")
            _draw_subdivision_warning(
                editable,
                properties.selected_subdivision_viewport,
                properties.selected_subdivision_render,
            )
            row = editable.row(align=True)
            row.operator("blender_terrain.apply_selected_settings")
            row.operator("blender_terrain.restore_selected_settings")
            if properties.active_import_representation == "BAKED":
                controls.label(text="Legacy baked terrain: controls unavailable", icon="INFO")

        result = self.layout.box()
        result.label(text=properties.validation_message)
        if properties.is_valid:
            result.label(text=properties.crs_summary)
            result.label(text=f"Area: {properties.area_square_metres / 1_000_000:.3f} km²")
            result.label(text=f"Elevation: {properties.selected_resolution:g} m")
            result.label(text=f"Samples: {properties.sample_count:,}")
            result.label(text=f"Terrain objects: {properties.terrain_tile_count}")
            result.label(text=properties.terrain_tile_summary)
            result.label(text=f"Estimated memory: {properties.estimated_memory_mib:.1f} MiB+")
            result.label(text=properties.imagery_summary)
            if properties.planning_warning:
                result.label(text=properties.planning_warning, icon="INFO")


def _job_state_label(state: str) -> str:
    return {
        "VALIDATING": "Validating",
        "DISCOVERING": "Finding sources",
        "DOWNLOADING_ELEVATION": "Downloading elevation",
        "DOWNLOADING_IMAGERY": "Downloading PNOA imagery",
        "PROCESSING_ELEVATION": "Processing elevation",
    }.get(state, state.replace("_", " ").title())


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
