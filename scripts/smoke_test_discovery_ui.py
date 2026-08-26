"""Exercise the UI discovery controller against the live CNIG service."""

from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import bpy


def main() -> None:
    arguments = sys.argv[sys.argv.index("--") + 1 :]
    if len(arguments) not in {1, 2}:
        raise RuntimeError("Expected the package target and optional product after --")
    target = arguments[0]
    if target.startswith("bl_ext."):
        extension = importlib.import_module(target)
    else:
        repository = Path(target).resolve()
        sys.path.insert(0, str(repository.parent))
        extension = importlib.import_module(repository.name)
    if not hasattr(bpy.types.Scene, "blender_terrain_roi"):
        extension.register()
        registered_here = True
    else:
        registered_here = False
    controller = extension.blender_terrain.ui.job_controller

    try:
        if not bpy.app.online_access:
            raise RuntimeError("Blender online access must be enabled for this smoke test")
        with TemporaryDirectory(ignore_cleanup_errors=True) as temporary_directory:
            controller._cache_directory = lambda _context: Path(temporary_directory)
            properties = bpy.context.scene.blender_terrain_roi
            properties.product = arguments[1] if len(arguments) == 2 else "MDT02"
            properties.elevation_resolution = "10"
            bpy.context.scene.blender_terrain_roi.imagery_gsd = "5"
            assert bpy.ops.blender_terrain.validate_roi() == {"FINISHED"}
            assert bpy.ops.blender_terrain.discover_sources() == {"FINISHED"}

            deadline = time.monotonic() + 60.0
            while controller.has_active_job() and time.monotonic() < deadline:
                time.sleep(0.1)
                controller._poll_active_job()

            assert not controller.has_active_job(), "Discovery did not finish within 60 seconds"
            assert properties.job_state == "COMPLETE", properties.job_message
            assert properties.discovered_file_count > 0
            assert properties.discovery_summary
            print(properties.discovery_summary)

            assert bpy.ops.blender_terrain.download_data() == {"FINISHED"}
            deadline = time.monotonic() + 600.0
            while controller.has_active_job() and time.monotonic() < deadline:
                time.sleep(0.1)
                controller._poll_active_job()

            assert not controller.has_active_job(), "Delivery did not finish within 600 seconds"
            assert properties.job_state == "COMPLETE", properties.job_message
            assert properties.delivery_ready
            assert properties.delivery_summary
            assert properties.imagery_available
            print(properties.delivery_summary)
            assert bpy.ops.blender_terrain.create_terrain() == {"FINISHED"}
            assert properties.terrain_created
    finally:
        if registered_here:
            extension.unregister()
    print("BlenderTerrain UI discovery smoke test passed")


if __name__ == "__main__":
    main()
