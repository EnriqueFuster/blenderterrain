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
        """Draw the intentionally minimal first extension panel."""

        self.layout.label(text="Terrain import tools are coming next.", icon="WORLD_DATA")
