"""Create Blender mesh objects from processed BlenderTerrain elevation arrays."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import bpy
import numpy as np

from ..core import build_terrain_mesh_geometry
from ..errors import RasterFormatError
from ..models import ProjectedBounds


def create_terrain_objects(
    context: bpy.types.Context, result_path: Path, vertical_scale: float
) -> tuple[bpy.types.Object, ...]:
    """Create one mesh object per processed tile using local projected coordinates."""

    result = _read_result(result_path)
    entries = result.get("processed_elevation")
    if not isinstance(entries, list) or not entries:
        raise RasterFormatError("Delivery result contains no processed elevation tiles")
    parsed = tuple(_parse_entry(entry) for entry in entries)
    origins = {
        epsg: (
            min(bounds.west for _, bounds, *_ in parsed if bounds.epsg == epsg),
            min(bounds.south for _, bounds, *_ in parsed if bounds.epsg == epsg),
        )
        for epsg in {bounds.epsg for _, bounds, *_ in parsed}
    }
    job_id = str(result.get("job_id", "unknown"))
    collection = bpy.data.collections.new(f"BlenderTerrain_{job_id[:8]}")
    context.scene.collection.children.link(collection)
    parents: dict[int, bpy.types.Object] = {}
    objects: list[bpy.types.Object] = []
    try:
        for epsg, (origin_x, origin_y) in origins.items():
            parent = bpy.data.objects.new(f"Terrain_EPSG_{epsg}", None)
            parent["blender_terrain_epsg"] = epsg
            parent["blender_terrain_origin_easting"] = origin_x
            parent["blender_terrain_origin_northing"] = origin_y
            collection.objects.link(parent)
            parents[epsg] = parent
        for index, (path, bounds, rows, columns, nodata) in enumerate(parsed):
            elevation = np.load(path, mmap_mode="r", allow_pickle=False)
            if elevation.dtype != np.float32 or elevation.shape != (rows + 1, columns + 1):
                raise RasterFormatError(f"Processed elevation dimensions do not match: {path.name}")
            geometry = build_terrain_mesh_geometry(elevation, bounds, nodata)
            mesh = _create_mesh(f"TerrainMesh_{index:03d}", geometry.vertices, geometry.faces)
            object_ = bpy.data.objects.new(f"Terrain_{index:03d}", mesh)
            origin_x, origin_y = origins[bounds.epsg]
            object_.location = (bounds.west - origin_x, bounds.south - origin_y, 0.0)
            object_.scale.z = vertical_scale
            object_.parent = parents[bounds.epsg]
            object_["blender_terrain_epsg"] = bounds.epsg
            object_["blender_terrain_west"] = bounds.west
            object_["blender_terrain_south"] = bounds.south
            object_["blender_terrain_east"] = bounds.east
            object_["blender_terrain_north"] = bounds.north
            object_["blender_terrain_source"] = str(path)
            collection.objects.link(object_)
            objects.append(object_)
    except BaseException:
        for object_ in tuple(collection.objects):
            mesh = object_.data if isinstance(object_.data, bpy.types.Mesh) else None
            bpy.data.objects.remove(object_, do_unlink=True)
            if mesh is not None:
                bpy.data.meshes.remove(mesh)
        bpy.data.collections.remove(collection)
        raise
    return tuple(objects)


def _create_mesh(name: str, vertices: Any, faces: Any) -> bpy.types.Mesh:
    mesh = bpy.data.meshes.new(name)
    mesh.vertices.add(len(vertices))
    mesh.vertices.foreach_set("co", vertices.ravel())
    mesh.loops.add(faces.size)
    mesh.loops.foreach_set("vertex_index", faces.ravel())
    mesh.polygons.add(len(faces))
    mesh.polygons.foreach_set("loop_start", np.arange(len(faces), dtype=np.int32) * 4)
    mesh.polygons.foreach_set("loop_total", np.full(len(faces), 4, dtype=np.int32))
    mesh.update(calc_edges=True)
    return mesh


def _read_result(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RasterFormatError("Cannot read the completed delivery result") from exc
    if not isinstance(payload, dict) or payload.get("state") != "COMPLETE":
        raise RasterFormatError("Delivery result is not complete")
    return payload


def _parse_entry(entry: object) -> tuple[Path, ProjectedBounds, int, int, float]:
    try:
        if not isinstance(entry, dict) or not isinstance(entry["bounds"], dict):
            raise TypeError
        raw_bounds = entry["bounds"]
        path = Path(entry["path"]).resolve()
        bounds = ProjectedBounds(
            float(raw_bounds["west"]), float(raw_bounds["south"]),
            float(raw_bounds["east"]), float(raw_bounds["north"]), int(raw_bounds["epsg"]),
        )
        rows = int(entry["rows"])
        columns = int(entry["columns"])
        nodata = float(entry["nodata"])
        if rows <= 0 or columns <= 0:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise RasterFormatError("Delivery result contains an invalid terrain tile") from exc
    return path, bounds, rows, columns, nodata
