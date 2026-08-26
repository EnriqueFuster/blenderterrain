"""Register and unregister the source extension in factory-startup Blender."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import bpy
import numpy as np


def main() -> None:
    arguments = sys.argv[sys.argv.index("--") + 1 :]
    if len(arguments) != 1:
        raise RuntimeError("Expected the repository path after --")
    repository = Path(arguments[0]).resolve()
    sys.path.insert(0, str(repository.parent))
    extension = importlib.import_module(repository.name)

    for _cycle in range(2):
        extension.register()
        addon = extension.blender_terrain.addon
        job_controller = extension.blender_terrain.ui.job_controller
        classes = addon.registered_class_types()
        assert all(class_type.is_registered for class_type in classes)
        assert classes[0].bl_idname == repository.name
        assert hasattr(bpy.types.Scene, "blender_terrain_roi")
        result = bpy.ops.blender_terrain.validate_roi()
        properties = bpy.context.scene.blender_terrain_roi
        assert result == {"FINISHED"}
        assert properties.is_valid
        assert properties.crs_summary == "EPSG:25830"
        assert properties.product == "MDT02"
        assert properties.selected_resolution == 2.0
        assert properties.area_square_metres > 0.0
        assert properties.sample_count > 0
        assert properties.terrain_tile_count == 6
        assert properties.estimated_memory_mib > 0.0
        assert "CNIG discovery" in properties.planning_warning
        assert properties.imagery_summary.startswith("PNOA 0.25 m:")
        assert not properties.job_active
        assert not job_controller.timer_is_registered()
        _smoke_terrain_operator(properties)
        extension.unregister()
        assert not any(class_type.is_registered for class_type in classes)
        assert not hasattr(bpy.types.Scene, "blender_terrain_roi")
        assert not job_controller.timer_is_registered()
    print("BlenderTerrain register/unregister smoke test passed")


def _smoke_terrain_operator(properties: object) -> None:
    with TemporaryDirectory() as temporary:
        directory = Path(temporary)
        array_path = directory / "terrain.npy"
        np.save(array_path, np.array([[1, 2], [3, 4]], dtype=np.float32))
        result_path = directory / "result.json"
        result_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "job_id": "12345678-test",
                    "state": "COMPLETE",
                    "processed_elevation": [
                        {
                            "path": str(array_path),
                            "bounds": {
                                "west": 700000,
                                "south": 4300000,
                                "east": 700010,
                                "north": 4300010,
                                "epsg": 25830,
                            },
                            "rows": 1,
                            "columns": 1,
                            "nodata": -9999,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        properties.delivery_result_path = str(result_path)
        assert bpy.ops.blender_terrain.create_terrain() == {"FINISHED"}
        terrain = bpy.data.objects["Terrain_000"]
        assert len(terrain.data.vertices) == 4
        assert len(terrain.data.polygons) == 1
        assert terrain["blender_terrain_epsg"] == 25830
        collection = bpy.data.collections["BlenderTerrain_12345678"]
        for object_ in tuple(collection.objects):
            mesh = object_.data if isinstance(object_.data, bpy.types.Mesh) else None
            bpy.data.objects.remove(object_, do_unlink=True)
            if mesh is not None:
                bpy.data.meshes.remove(mesh)
        bpy.data.collections.remove(collection)


if __name__ == "__main__":
    main()
