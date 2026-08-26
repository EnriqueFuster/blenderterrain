"""Load the packaged extension from a temporary Blender extension repository."""

from __future__ import annotations

import importlib
import sys

import bpy


def main() -> None:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    module_name = arguments[0] if arguments else "bl_ext.user_default.blender_terrain"
    extension = importlib.import_module(module_name)
    if not hasattr(bpy.types.Scene, "blender_terrain_roi"):
        extension.register()
        registered_here = True
    else:
        registered_here = False
    try:
        assert bpy.ops.blender_terrain.validate_roi() == {"FINISHED"}
        properties = bpy.context.scene.blender_terrain_roi
        assert properties.is_valid
        assert properties.product == "MDT02"
        assert hasattr(bpy.types.Scene, "blender_terrain_roi")
    finally:
        if registered_here:
            extension.unregister()
    print("Packaged BlenderTerrain extension smoke test passed")


if __name__ == "__main__":
    main()
