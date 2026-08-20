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
    if len(arguments) != 1:
        raise RuntimeError("Expected the repository path after --")
    repository = Path(arguments[0]).resolve()
    sys.path.insert(0, str(repository.parent))
    extension = importlib.import_module(repository.name)
    extension.register()
    controller = extension.blender_terrain.ui.job_controller

    try:
        if not bpy.app.online_access:
            raise RuntimeError("Blender online access must be enabled for this smoke test")
        with TemporaryDirectory() as temporary_directory:
            controller._cache_directory = lambda _context: Path(temporary_directory)
            assert bpy.ops.blender_terrain.validate_roi() == {"FINISHED"}
            assert bpy.ops.blender_terrain.discover_sources() == {"FINISHED"}

            deadline = time.monotonic() + 60.0
            while controller.has_active_job() and time.monotonic() < deadline:
                time.sleep(0.1)
                controller._poll_active_job()

            properties = bpy.context.scene.blender_terrain_roi
            assert not controller.has_active_job(), "Discovery did not finish within 60 seconds"
            assert properties.job_state == "COMPLETE", properties.job_message
            assert properties.discovered_file_count > 0
            assert properties.discovery_summary
            print(properties.discovery_summary)
    finally:
        extension.unregister()
    print("BlenderTerrain UI discovery smoke test passed")


if __name__ == "__main__":
    main()
