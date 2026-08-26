"""Sidebar panels for the BlenderTerrain extension."""

from __future__ import annotations

import bpy


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
        row = box.row(align=True)
        row.prop(properties, "west")
        row.prop(properties, "east")
        row = box.row(align=True)
        row.prop(properties, "south")
        row.prop(properties, "north")

        elevation = self.layout.box()
        elevation.enabled = not properties.job_active
        elevation.label(text="Elevation", icon="MOD_DISPLACE")
        elevation.prop(properties, "product")
        elevation.prop(properties, "elevation_resolution")

        imagery = self.layout.box()
        imagery.enabled = not properties.job_active
        imagery.label(text="Imagery", icon="IMAGE_DATA")
        imagery.prop(properties, "use_imagery")
        if properties.use_imagery:
            imagery.prop(properties, "imagery_gsd")

        actions = self.layout.box()
        actions.label(text="Data Sources", icon="URL")
        if properties.job_active:
            actions.prop(properties, "job_progress", text=properties.job_state, slider=True)
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

        result = self.layout.box()
        result.label(text=properties.validation_message)
        if properties.is_valid:
            result.label(text=properties.crs_summary)
            result.label(text=f"Area: {properties.area_square_metres / 1_000_000:.3f} km²")
            result.label(text=f"Elevation: {properties.selected_resolution:g} m")
            result.label(text=f"Samples: {properties.sample_count:,}")
            result.label(text=f"Terrain objects: {properties.terrain_tile_count}")
            result.label(text=f"Estimated memory: {properties.estimated_memory_mib:.1f} MiB+")
            result.label(text=properties.imagery_summary)
            if properties.planning_warning:
                result.label(text=properties.planning_warning, icon="INFO")
