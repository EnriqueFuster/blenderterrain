"""Load the packaged extension from a temporary Blender extension repository."""

from __future__ import annotations

import importlib
import sys

import bpy


def main() -> None:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    module_name = arguments[0] if arguments else "bl_ext.user_default.blender_terrain"
    if len(arguments) > 1:
        sys.path[:0] = arguments[1:]
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
        package_name = f"{module_name}.blender_terrain"
        catalog = importlib.import_module(f"{package_name}.catalog")
        registry = importlib.import_module(f"{package_name}.providers.registry")
        osni = catalog.load_bundled_catalog().product("GB_NIR_OSNI_10M_DTM")
        assert osni.selectable
        assert "osni" in registry.build_raster_acquirers(("osni",))
        if len(arguments) > 2:
            from math import isclose

            from pyproj import __version__ as pyproj_version

            assert pyproj_version == "3.7.2"
            british_grid = importlib.import_module(f"{package_name}.providers.british_grid")
            easting, northing = british_grid.BritishGridTransform(
                british_grid.bundled_ostn15_path()
            ).forward(-3.18, 51.48)
            assert isclose(easting, 318153.2407, abs_tol=0.001)
            assert isclose(northing, 176331.9192, abs_tol=0.001)
    finally:
        if registered_here:
            extension.unregister()
    print("Packaged BlenderTerrain extension smoke test passed")


if __name__ == "__main__":
    main()
