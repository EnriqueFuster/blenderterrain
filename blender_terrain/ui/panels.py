"""Sidebar panels for the BlenderTerrain extension."""

from __future__ import annotations

import bpy


class BLENDERTERRAIN_PT_main(bpy.types.Panel):
    """Display the initial BlenderTerrain sidebar placeholder."""

    bl_idname = "BLENDERTERRAIN_PT_main"
    bl_label = "BlenderTerrain"
    bl_category = "Terrain"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context: bpy.types.Context) -> None:
        """Draw manual ROI input and its latest offline estimate."""

        properties = context.scene.blender_terrain_roi
        box = self.layout.box()
        box.label(text="Area of Interest", icon="WORLD_DATA")
        row = box.row(align=True)
        row.prop(properties, "west")
        row.prop(properties, "east")
        row = box.row(align=True)
        row.prop(properties, "south")
        row.prop(properties, "north")
        box.operator("blender_terrain.validate_roi", icon="CHECKMARK")

        result = self.layout.box()
        result.label(text=properties.validation_message)
        if properties.is_valid:
            result.label(text=properties.crs_summary)
            result.label(text=f"Area: {properties.area_square_metres / 1_000_000:.3f} km²")
            result.label(text=f"2 m samples: {properties.sample_count:,}")
