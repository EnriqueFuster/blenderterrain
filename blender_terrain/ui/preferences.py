"""User preferences owned by the BlenderTerrain extension."""

from __future__ import annotations

import bpy
from bpy.props import StringProperty


class BLENDERTERRAIN_AddonPreferences(bpy.types.AddonPreferences):
    """Persist the user-selected directory for downloaded geographic data."""

    bl_idname = "blender_terrain"

    cache_directory: StringProperty(
        name="Cache Directory",
        description="Directory for downloaded source data and generated assets",
        subtype="DIR_PATH",
        default="",
    )

    def draw(self, context: bpy.types.Context) -> None:
        """Draw extension preferences."""

        self.layout.prop(self, "cache_directory")
        if not self.cache_directory:
            self.layout.label(
                text="A cache directory will be required before importing.", icon="INFO"
            )
