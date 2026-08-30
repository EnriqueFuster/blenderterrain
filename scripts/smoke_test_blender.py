"""Register and unregister the source extension in factory-startup Blender."""

from __future__ import annotations

import importlib
import json
import math
import struct
import sys
import zlib
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

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
        panel_ids = {getattr(class_type, "bl_idname", "") for class_type in classes}
        assert "BLENDERTERRAIN_PT_source" in panel_ids
        assert "BLENDERTERRAIN_PT_area" not in panel_ids
        assert "BLENDERTERRAIN_PT_acquisition" not in panel_ids
        assert hasattr(bpy.types.Scene, "blender_terrain_roi")
        properties = bpy.context.scene.blender_terrain_roi
        property_rna = type(properties).bl_rna.properties
        assert properties.use_bathymetry
        assert property_rna["terrain_subdivision_viewport"].hard_max == 11
        assert property_rna["terrain_subdivision_render"].hard_max == 11
        if _cycle == 1:
            properties.roi_input_mode = "CENTER_SIZE"
            properties.center_longitude = -0.38
            properties.center_latitude = 39.47
            properties.roi_width_metres = 2_000.0
            properties.roi_height_metres = 2_000.0
            properties.tiling_mode = "MANUAL"
            properties.manual_tile_rows = 2
            properties.manual_tile_columns = 3
            assert bpy.ops.blender_terrain.update_bbox_from_center() == {"FINISHED"}
        result = bpy.ops.blender_terrain.validate_roi()
        assert result == {"FINISHED"}
        assert properties.is_valid
        assert properties.crs_summary == "EPSG:25830"
        assert properties.product == "MDT02"
        spanish_region = (
            extension.blender_terrain.core.RegionOfInterest.from_geojson_geometry(
                json.loads(properties.roi_geometry_json)
            )
        )
        spanish_plan = job_controller._acquisition_plan_from_properties(
            properties, spanish_region
        )
        assert {
            selection.product_id for selection in spanish_plan.selections.selections
        } == {"MDT02", "PNOA_MA", "GEBCO_2026"}
        assert properties.selected_resolution == 2.0
        assert properties.area_square_metres > 0.0
        assert properties.sample_count > 0
        assert properties.terrain_tile_count == 6
        assert properties.terrain_tile_summary.startswith("Largest object:")
        assert properties.estimated_memory_mib > 0.0
        assert "provider discovery" in properties.planning_warning
        map_geometry = properties.roi_geometry_json
        properties.roi_input_mode = "MAP_POLYGON"
        properties.roi_geometry_json = map_geometry
        properties.product = "MDS05"
        assert properties.roi_geometry_json == map_geometry
        assert bpy.ops.blender_terrain.validate_roi() == {"FINISHED"}
        assert properties.roi_geometry_json == map_geometry
        properties.product = "MDT02"
        assert bpy.ops.blender_terrain.validate_roi() == {"FINISHED"}
        assert properties.imagery_summary.startswith("PNOA 0.25 m:")
        original_geometry = properties.roi_geometry_json
        precise_west = float(properties.west) + 1e-10
        properties.roi_geometry_json = json.dumps(
            {
                "type": "Polygon",
                "coordinates": [[
                    [precise_west, properties.south],
                    [properties.east, properties.south],
                    [properties.east, properties.north],
                    [precise_west, properties.north],
                    [precise_west, properties.south],
                ]],
            }
        )
        precision_job = job_controller._job_from_properties(
            str(uuid4()), str(uuid4()), properties
        )
        assert precision_job.region is not None
        assert precision_job.bounds == precision_job.region.bounds
        properties.roi_geometry_json = original_geometry
        assert bpy.ops.blender_terrain.copy_bbox() == {"FINISHED"}
        assert not properties.job_active
        assert not job_controller.timer_is_registered()
        properties.job_active = True
        properties.active_job_mode = "delivery"
        properties.delivery_ready = True
        assert job_controller.recover_interrupted_jobs() == 1
        assert not properties.job_active
        assert not properties.delivery_ready
        assert properties.job_state == "INVALID_DATA"
        assert "interrupted" in properties.job_message
        _smoke_terrain_operator(properties, use_imagery=_cycle == 0)
        properties = bpy.context.scene.blender_terrain_roi
        properties.product = "COPERNICUS_GLO30_2021"
        assert bpy.ops.blender_terrain.validate_roi() == {"FINISHED"}
        assert properties.selected_resolution == 30.0
        assert bpy.ops.blender_terrain.discover_sources() == {"FINISHED"}
        assert properties.discovery_ready
        assert "Copernicus GLO-30" in properties.discovery_summary
        assert "GEBCO" in properties.discovery_summary
        region = extension.blender_terrain.core.RegionOfInterest.from_geojson_geometry(
            json.loads(properties.roi_geometry_json)
        )
        global_plan = job_controller._acquisition_plan_from_properties(properties, region)
        assert global_plan.selections.for_kind(
            extension.blender_terrain.catalog.DatasetKind.BATHYMETRY
        ).product_id == "GEBCO_2026"
        assert not properties.job_active
        properties.roi_input_mode = "BOUNDING_BOX"
        properties.west = 2.34
        properties.south = 48.85
        properties.east = 2.36
        properties.north = 48.87
        assert bpy.ops.blender_terrain.validate_roi() == {"FINISHED"}
        assert json.loads(properties.available_product_ids_json) == [
            "COPERNICUS_GLO30_2021",
            "GEDTM30_V11",
        ]
        assert properties.product == "COPERNICUS_GLO30_2021"
        properties.product = "GEDTM30_V11"
        assert bpy.ops.blender_terrain.validate_roi() == {"FINISHED"}
        assert properties.selected_resolution == 30.0
        assert bpy.ops.blender_terrain.discover_sources() == {"FINISHED"}
        assert "GEDTM30" in properties.discovery_summary
        properties.available_product_ids_json = "[]"
        properties.product = "MDT02"
        properties.use_imagery = True
        properties.elevation_source = "LOCAL"
        assert properties.elevation_source == "LOCAL"
        properties.elevation_source = "CNIG"
        extension.unregister()
        assert not any(class_type.is_registered for class_type in classes)
        assert not hasattr(bpy.types.Scene, "blender_terrain_roi")
        assert not job_controller.timer_is_registered()
    print("BlenderTerrain register/unregister smoke test passed")


def _smoke_terrain_operator(properties: object, use_imagery: bool) -> None:
    with TemporaryDirectory() as temporary:
        directory = Path(temporary)
        array_path = directory / "terrain.npy"
        marine_mask_path = directory / "marine-mask.npy"
        image_paths = (directory / "pnoa-west.png", directory / "pnoa-east.png")
        elevation = np.array([[1, 2], [3, 4]], dtype=np.float32)
        if use_imagery:
            elevation = np.linspace(1, 4, 17 * 17, dtype=np.float32).reshape(17, 17)
            elevation[0, 0] = 1
            elevation[0, -1] = 2
            elevation[-1, 0] = 3
            elevation[-1, -1] = 4
            elevation[8, 8] = 10
        np.save(array_path, elevation)
        if use_imagery:
            marine_mask = np.zeros(elevation.shape, dtype=np.uint8)
            marine_mask[:, elevation.shape[1] // 2 :] = 1
            np.save(marine_mask_path, marine_mask)
        if use_imagery:
            for image_path in image_paths:
                _write_png(image_path)
        result_path = directory / "result.json"
        result_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "task_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "import_id": "12345678-1234-4234-8234-123456789abc",
                    "state": "COMPLETE",
                    "request": {
                        "bounds_wgs84": {
                            "west": -0.381,
                            "south": 39.469,
                            "east": -0.379,
                            "north": 39.471,
                        },
                        "product": "MDT02",
                        "elevation_resolution_metres": 10.0,
                        "use_imagery": use_imagery,
                        "use_bathymetry": use_imagery,
                        "imagery_gsd_metres": 1.0 if use_imagery else None,
                    },
                    "crs": [
                        {
                            "epsg": 25830,
                            "name": "ETRS89 / UTM zone 30N",
                            "datum": "ETRS89",
                            "utm_zone": 30,
                        }
                    ],
                    "sources": [
                        {
                            "product": "MDT02",
                            "filename": "source.tif",
                            "sequential_id": "1",
                        }
                    ],
                    "provenance": {
                        "source": "Instituto Geográfico Nacional de España (IGN-CNIG)",
                        "data_policy_url": (
                            "https://centrodedescargas.cnig.es/"
                            "CentroDescargas/politica-datos"
                        ),
                        "license": "CC BY 4.0-compatible IGN-CNIG data terms",
                        "retrieved_at_utc": "2026-08-26T00:00:00+00:00",
                    },
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
                            "rows": elevation.shape[0] - 1,
                            "columns": elevation.shape[1] - 1,
                            "nodata": -9999,
                            **(
                                {"marine_mask_path": str(marine_mask_path)}
                                if use_imagery
                                else {}
                            ),
                        }
                    ],
                    "imagery": [
                        {
                            "path": str(image_paths[0]),
                            "bounds": {
                                "west": 700000,
                                "south": 4300000,
                                "east": 700005,
                                "north": 4300010,
                                "epsg": 25830,
                            },
                        },
                        {
                            "path": str(image_paths[1]),
                            "bounds": {
                                "west": 700005,
                                "south": 4300000,
                                "east": 700010,
                                "north": 4300010,
                                "epsg": 25830,
                            },
                        }
                    ] if use_imagery else [],
                }
            ),
            encoding="utf-8",
        )
        properties.delivery_result_path = str(result_path)
        properties.import_id = "12345678-1234-4234-8234-123456789abc"
        properties.full_resolution_mesh = not use_imagery
        assert bpy.ops.blender_terrain.create_terrain() == {"FINISHED"}
        terrain = bpy.data.objects["BT_12345678_Terrain_000"]
        assert bpy.context.view_layer.objects.active == terrain
        assert terrain.select_get()
        assert len(terrain.data.vertices) == 4
        assert len(terrain.data.polygons) == 1
        assert terrain["blender_terrain_epsg"] == 25830
        assert terrain["blender_terrain_schema_version"] == 2
        assert terrain["blender_terrain_representation"] == "DISPLACEMENT"
        assert terrain["blender_terrain_strength_multiplier"] == 1.0
        assert tuple(modifier.type for modifier in terrain.modifiers) == (
            "SUBSURF",
            "DISPLACE",
        )
        assert terrain.modifiers[0].subdivision_type == "SIMPLE"
        assert terrain.modifiers[0].levels == (0 if properties.full_resolution_mesh else 1)
        assert terrain.modifiers[1].texture_coords == "UV"
        assert terrain.modifiers[1].uv_layer == "TerrainUV"
        assert properties.active_import_id == properties.import_id
        assert properties.active_import_representation == "DISPLACEMENT"
        assert (
            properties.active_import_full_resolution_mesh
            == properties.full_resolution_mesh
        )
        if not properties.full_resolution_mesh:
            _assert_progressive_peak(terrain)
        properties.terrain_vertical_scale = 1.5
        properties.terrain_strength_multiplier = 1.1
        properties.terrain_displacement_midlevel = 0.2
        properties.terrain_subdivision_viewport = 0
        properties.terrain_subdivision_render = 2
        properties.terrain_displacement_enabled = True
        assert bpy.ops.blender_terrain.apply_import_settings() == {"FINISHED"}
        assert terrain.scale.z == 1.5
        assert terrain.modifiers[0].levels == 0
        assert terrain.modifiers[0].render_levels == 2
        expected_range = 9.0 if use_imagery else 3.0
        assert math.isclose(
            terrain.modifiers[1].strength, expected_range * 1.1, rel_tol=1e-6
        )
        assert math.isclose(properties.selected_strength_multiplier, 1.1, rel_tol=1e-6)
        assert math.isclose(properties.selected_displacement_midlevel, 0.2, rel_tol=1e-6)
        assert properties.selected_subdivision_viewport == 0
        assert properties.selected_subdivision_render == 2
        properties.selected_strength_multiplier = 1.25
        properties.selected_displacement_midlevel = 0.3
        properties.selected_subdivision_viewport = 1
        properties.selected_subdivision_render = 3
        assert bpy.ops.blender_terrain.apply_selected_settings() == {"FINISHED"}
        assert terrain["blender_terrain_strength_multiplier"] == 1.25
        assert math.isclose(
            terrain.modifiers[1].strength, expected_range * 1.25, rel_tol=1e-6
        )
        assert math.isclose(terrain.modifiers[1].mid_level, 0.3, rel_tol=1e-6)
        assert terrain.modifiers[0].levels == 1
        assert bpy.ops.blender_terrain.restore_selected_settings() == {"FINISHED"}
        assert math.isclose(
            terrain["blender_terrain_strength_multiplier"], 1.1, rel_tol=1e-6
        )
        assert math.isclose(
            terrain.modifiers[1].strength, expected_range * 1.1, rel_tol=1e-6
        )
        assert math.isclose(properties.selected_strength_multiplier, 1.1, rel_tol=1e-6)
        assert math.isclose(properties.selected_displacement_midlevel, 0.2, rel_tol=1e-6)
        assert terrain.modifiers[0].levels == 0
        assert bpy.ops.blender_terrain.select_import_objects() == {"FINISHED"}
        _assert_evaluated_elevation(
            terrain,
            strength_multiplier=1.1,
            midlevel=0.2,
            elevation_range=expected_range,
        )
        assert len(terrain.data.materials) == (1 if use_imagery else 0)
        if use_imagery:
            assert terrain.data.materials[0].use_nodes
            image_nodes = tuple(
                node
                for node in terrain.data.materials[0].node_tree.nodes
                if node.bl_idname == "ShaderNodeTexImage"
            )
            assert len(image_nodes) == 3
            marine_node = terrain.data.materials[0].node_tree.nodes["Marine_Mask"]
            assert marine_node.image.colorspace_settings.name == "Non-Color"
            assert marine_node.interpolation == "Closest"
            assert all(
                node.image.colorspace_settings.name == "sRGB"
                for node in image_nodes
                if node != marine_node
            )
            mix_nodes = tuple(
                node
                for node in terrain.data.materials[0].node_tree.nodes
                if node.bl_idname == "ShaderNodeMixRGB"
            )
            assert len(mix_nodes) == 2
            marine_mix = terrain.data.materials[0].node_tree.nodes["Land_Seabed_Mix"]
            assert marine_mix.inputs[0].is_linked
            assert all(
                math.isclose(actual, expected, rel_tol=1e-6)
                for actual, expected in zip(
                    marine_mix.inputs[2].default_value,
                    (0.18, 0.07, 0.025, 1.0),
                    strict=True,
                )
            )
            assert bpy.ops.blender_terrain.pack_imagery() == {"FINISHED"}
            assert all(node.image.packed_file is not None for node in image_nodes)
        collection = bpy.data.collections["BlenderTerrain_12345678"]
        assert collection["blender_terrain_import_id"] == properties.import_id
        assert collection["blender_terrain_product"] == "MDT02"
        assert collection["blender_terrain_schema_version"] == 2
        assert collection["blender_terrain_representation"] == "DISPLACEMENT"
        assert (
            collection["blender_terrain_full_resolution_mesh"]
            == properties.full_resolution_mesh
        )
        assert collection["blender_terrain_vertical_scale"] == 1.5
        assert math.isclose(
            collection["blender_terrain_strength_multiplier"], 1.1, rel_tol=1e-6
        )
        assert math.isclose(
            collection["blender_terrain_displacement_midlevel"], 0.2, rel_tol=1e-6
        )
        assert collection["blender_terrain_elevation_minimum"] == 1.0
        assert collection["blender_terrain_elevation_maximum"] == (10.0 if use_imagery else 4.0)
        assert collection["blender_terrain_source"].startswith(
            "Instituto Geográfico Nacional"
        )
        assert any(
            candidate.get("blender_terrain_import_id") == properties.import_id
            for candidate in bpy.data.collections
        )
        if use_imagery:
            blend_path = directory / "displacement-terrain.blend"
            assert bpy.ops.wm.save_as_mainfile(filepath=str(blend_path)) == {"FINISHED"}
            assert bpy.ops.wm.open_mainfile(filepath=str(blend_path)) == {"FINISHED"}
            terrain = bpy.data.objects["BT_12345678_Terrain_000"]
            _assert_evaluated_elevation(
                terrain,
                strength_multiplier=1.1,
                midlevel=0.2,
                elevation_range=expected_range,
            )
            assert terrain.modifiers[1].texture.image.packed_file is not None
            properties = bpy.context.scene.blender_terrain_roi
            assert properties.active_import_id == "12345678-1234-4234-8234-123456789abc"
            collection = bpy.data.collections["BlenderTerrain_12345678"]
        materials = tuple(
            material
            for object_ in collection.objects
            if isinstance(object_.data, bpy.types.Mesh)
            for material in object_.data.materials
        )
        heightmap_textures = tuple(
            modifier.texture
            for object_ in collection.objects
            for modifier in object_.modifiers
            if modifier.type == "DISPLACE"
        )
        heightmap_images = tuple(texture.image for texture in heightmap_textures)
        for object_ in tuple(collection.objects):
            mesh = object_.data if isinstance(object_.data, bpy.types.Mesh) else None
            bpy.data.objects.remove(object_, do_unlink=True)
            if mesh is not None:
                bpy.data.meshes.remove(mesh)
        bpy.data.collections.remove(collection)
        for material in materials:
            bpy.data.materials.remove(material)
        for texture in heightmap_textures:
            bpy.data.textures.remove(texture)
        for image in heightmap_images:
            bpy.data.images.remove(image)
        if use_imagery:
            for image_path in image_paths:
                bpy.data.images.remove(bpy.data.images[image_path.name])


def _assert_evaluated_elevation(
    terrain: bpy.types.Object,
    *,
    strength_multiplier: float = 1.0,
    midlevel: float = 0.0,
    elevation_range: float = 1.0,
) -> None:
    evaluated = terrain.evaluated_get(bpy.context.evaluated_depsgraph_get())
    evaluated_mesh = evaluated.to_mesh()
    try:
        np.testing.assert_allclose(
            [vertex.co.z for vertex in evaluated_mesh.vertices],
            [
                1.0 - midlevel * elevation_range * strength_multiplier
                + value * strength_multiplier
                for value in (0.0, 1.0, 2.0, 3.0)
            ],
            atol=1e-5,
        )
    finally:
        evaluated.to_mesh_clear()


def _assert_progressive_peak(terrain: bpy.types.Object) -> None:
    evaluated = terrain.evaluated_get(bpy.context.evaluated_depsgraph_get())
    evaluated_mesh = evaluated.to_mesh()
    try:
        assert len(terrain.data.vertices) == 4
        assert len(evaluated_mesh.vertices) == 9
        assert max(vertex.co.z for vertex in evaluated_mesh.vertices) > 9.9
    finally:
        evaluated.to_mesh_clear()


def _write_png(path: Path) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        checksum = zlib.crc32(data, zlib.crc32(kind))
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )


if __name__ == "__main__":
    main()
