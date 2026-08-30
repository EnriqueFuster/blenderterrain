"""Create Blender mesh objects from processed BlenderTerrain elevation arrays."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import bpy
import numpy as np

from ..core import (
    DEFAULT_PREVIEW_SUBDIVISION_LEVEL,
    PREVIEW_MESH_REDUCTION_FACTOR,
    TERRAIN_SCHEMA_VERSION,
    TerrainRepresentation,
    bounds_fully_covered,
    build_displacement_mesh_geometry,
    calculate_elevation_range,
    normalize_heightmap,
    projected_texture_transform,
)
from ..errors import RasterFormatError
from ..models import ProjectedBounds


@dataclass(frozen=True, slots=True)
class _ImageryEntry:
    path: Path
    bounds: ProjectedBounds


def create_terrain_objects(
    context: bpy.types.Context,
    result_path: Path,
    vertical_scale: float,
    pack_images: bool = False,
    full_resolution_mesh: bool = False,
    progress_callback: Callable[[float, str], None] | None = None,
) -> tuple[bpy.types.Object, ...]:
    """Create one mesh object per processed tile using local projected coordinates."""

    _report_build_progress(progress_callback, 0.0, "Reading processed terrain data")
    result = _read_result(result_path)
    entries = result.get("processed_elevation")
    if not isinstance(entries, list) or not entries:
        raise RasterFormatError("Delivery result contains no processed elevation tiles")
    parsed = tuple(_parse_entry(entry) for entry in entries)
    imagery = _parse_imagery(result.get("imagery", []))
    request, provenance, sources, crs = _parse_manifest(result)
    origins = {
        epsg: (
            min(bounds.west for _, bounds, *_ in parsed if bounds.epsg == epsg),
            min(bounds.south for _, bounds, *_ in parsed if bounds.epsg == epsg),
        )
        for epsg in {bounds.epsg for _, bounds, *_ in parsed}
    }
    loaded = tuple(
        (np.load(path, mmap_mode="r", allow_pickle=False), nodata)
        for path, _bounds, _rows, _columns, nodata, _marine_mask_path in parsed
    )
    for (elevation, _nodata), (
        path,
        _bounds,
        rows,
        columns,
        _entry_nodata,
        _marine_mask_path,
    ) in zip(
        loaded, parsed, strict=True
    ):
        if elevation.dtype != np.float32 or elevation.shape != (rows + 1, columns + 1):
            raise RasterFormatError(f"Processed elevation dimensions do not match: {path.name}")
    _report_build_progress(progress_callback, 0.1, "Calculating shared elevation range")
    elevation_range = calculate_elevation_range(loaded)
    import_id = str(result["import_id"])
    task_id = str(result["task_id"])
    short_id = import_id[:8]
    collection = bpy.data.collections.new(f"BlenderTerrain_{short_id}")
    collection["blender_terrain_schema_version"] = TERRAIN_SCHEMA_VERSION
    collection["blender_terrain_representation"] = (
        TerrainRepresentation.DISPLACEMENT.value
    )
    collection["blender_terrain_import_id"] = import_id
    collection["blender_terrain_task_id"] = task_id
    collection["blender_terrain_product"] = request["product"]
    collection["blender_terrain_elevation_resolution_metres"] = request[
        "elevation_resolution_metres"
    ]
    collection["blender_terrain_use_imagery"] = request["use_imagery"]
    collection["blender_terrain_use_bathymetry"] = request.get(
        "use_bathymetry", False
    )
    if request.get("manual_tile_rows") is not None:
        collection["blender_terrain_manual_tile_rows"] = request["manual_tile_rows"]
        collection["blender_terrain_manual_tile_columns"] = request[
            "manual_tile_columns"
        ]
    if request["imagery_gsd_metres"] is not None:
        collection["blender_terrain_imagery_gsd_metres"] = request[
            "imagery_gsd_metres"
        ]
    collection["blender_terrain_roi_wgs84"] = json.dumps(
        request.get("roi_geometry_wgs84") or request["bounds_wgs84"], sort_keys=True
    )
    collection["blender_terrain_crs"] = json.dumps(crs, ensure_ascii=False, sort_keys=True)
    collection["blender_terrain_sources"] = json.dumps(
        sources, ensure_ascii=False, sort_keys=True
    )
    collection["blender_terrain_source"] = provenance["source"]
    collection["blender_terrain_attribution"] = str(
        provenance.get("attribution", provenance["source"])
    )
    collection["blender_terrain_data_policy_url"] = provenance["data_policy_url"]
    collection["blender_terrain_data_license"] = provenance["license"]
    collection["blender_terrain_retrieved_at_utc"] = provenance["retrieved_at_utc"]
    collection["blender_terrain_vertical_scale"] = vertical_scale
    collection["blender_terrain_strength_multiplier"] = 1.0
    collection["blender_terrain_displacement_midlevel"] = 0.0
    initial_subdivision = 0 if full_resolution_mesh else DEFAULT_PREVIEW_SUBDIVISION_LEVEL
    collection["blender_terrain_subdivision_viewport"] = initial_subdivision
    collection["blender_terrain_subdivision_render"] = initial_subdivision
    collection["blender_terrain_displacement_enabled"] = True
    collection["blender_terrain_full_resolution_mesh"] = full_resolution_mesh
    collection["blender_terrain_base_mesh_reduction_factor"] = (
        1 if full_resolution_mesh else PREVIEW_MESH_REDUCTION_FACTOR
    )
    collection["blender_terrain_elevation_minimum"] = elevation_range.minimum
    collection["blender_terrain_elevation_maximum"] = elevation_range.maximum
    collection["blender_terrain_elevation_range"] = elevation_range.span
    context.scene.collection.children.link(collection)
    _report_build_progress(progress_callback, 0.15, "Creating terrain collection")
    parents: dict[int, bpy.types.Object] = {}
    objects: list[bpy.types.Object] = []
    materials: list[bpy.types.Material] = []
    heightmap_images: list[bpy.types.Image] = []
    heightmap_textures: list[bpy.types.Texture] = []
    marine_mask_images: list[bpy.types.Image] = []
    try:
        for epsg, (origin_x, origin_y) in origins.items():
            parent = bpy.data.objects.new(f"BT_{short_id}_EPSG_{epsg}", None)
            parent["blender_terrain_import_id"] = import_id
            parent["blender_terrain_epsg"] = epsg
            parent["blender_terrain_origin_easting"] = origin_x
            parent["blender_terrain_origin_northing"] = origin_y
            collection.objects.link(parent)
            parents[epsg] = parent
        for index, (
            (elevation, nodata),
            (path, bounds, _rows, _columns, _, marine_mask_path),
        ) in enumerate(
            zip(loaded, parsed, strict=True)
        ):
            _report_build_progress(
                progress_callback,
                0.15 + 0.75 * index / len(parsed),
                f"Creating terrain object {index + 1} of {len(parsed)}",
            )
            geometry = build_displacement_mesh_geometry(
                elevation,
                bounds,
                nodata,
                elevation_range.minimum,
                reduction_factor=(
                    1 if full_resolution_mesh else PREVIEW_MESH_REDUCTION_FACTOR
                ),
            )
            if geometry.faces.size == 0:
                continue
            mesh = _create_mesh(
                f"BT_{short_id}_Mesh_{index:03d}",
                geometry.vertices,
                geometry.faces,
                uv_shape=elevation.shape,
            )
            object_ = bpy.data.objects.new(f"BT_{short_id}_Terrain_{index:03d}", mesh)
            origin_x, origin_y = origins[bounds.epsg]
            object_.location = (bounds.west - origin_x, bounds.south - origin_y, 0.0)
            object_.scale.z = vertical_scale
            object_.parent = parents[bounds.epsg]
            object_["blender_terrain_epsg"] = bounds.epsg
            object_["blender_terrain_schema_version"] = TERRAIN_SCHEMA_VERSION
            object_["blender_terrain_representation"] = (
                TerrainRepresentation.DISPLACEMENT.value
            )
            object_["blender_terrain_strength_multiplier"] = 1.0
            object_["blender_terrain_displacement_midlevel"] = 0.0
            object_["blender_terrain_full_resolution_mesh"] = full_resolution_mesh
            object_["blender_terrain_heightmap_rows"] = elevation.shape[0]
            object_["blender_terrain_heightmap_columns"] = elevation.shape[1]
            object_["blender_terrain_elevation_minimum"] = elevation_range.minimum
            object_["blender_terrain_elevation_range"] = elevation_range.span
            object_["blender_terrain_west"] = bounds.west
            object_["blender_terrain_south"] = bounds.south
            object_["blender_terrain_east"] = bounds.east
            object_["blender_terrain_north"] = bounds.north
            object_["blender_terrain_source"] = str(path)
            object_["blender_terrain_import_id"] = import_id
            object_["blender_terrain_task_id"] = task_id
            heightmap = normalize_heightmap(elevation, nodata, elevation_range)
            image = _create_heightmap_image(
                f"BT_{short_id}_Heightmap_{index:03d}", heightmap
            )
            texture = bpy.data.textures.new(
                f"BT_{short_id}_Displacement_{index:03d}", type="IMAGE"
            )
            texture.image = image
            texture.extension = "EXTEND"
            heightmap_images.append(image)
            heightmap_textures.append(texture)
            subdivision = object_.modifiers.new("Terrain Subdivision", "SUBSURF")
            subdivision.subdivision_type = "SIMPLE"
            subdivision.levels = initial_subdivision
            subdivision.render_levels = initial_subdivision
            displacement = object_.modifiers.new("Terrain Displacement", "DISPLACE")
            displacement.texture = texture
            displacement.texture_coords = "UV"
            displacement.uv_layer = "TerrainUV"
            displacement.direction = "Z"
            displacement.mid_level = 0.0
            displacement.strength = elevation_range.span
            marine_mask_image = None
            if marine_mask_path is not None:
                marine_mask = _load_marine_mask(marine_mask_path, elevation.shape)
                marine_mask_image = _create_marine_mask_image(
                    f"BT_{short_id}_MarineMask_{index:03d}", marine_mask
                )
                marine_mask_images.append(marine_mask_image)
                object_["blender_terrain_marine_mask"] = str(marine_mask_path)
            material = _create_imagery_material(
                f"BT_{short_id}_Material_{index:03d}",
                bounds,
                imagery,
                import_id,
                marine_mask_image,
            )
            if material is not None:
                mesh.materials.append(material)
                materials.append(material)
            collection.objects.link(object_)
            objects.append(object_)
        if not objects:
            raise RasterFormatError("ROI does not contain terrain geometry")
    except BaseException:
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
        for image in marine_mask_images:
            bpy.data.images.remove(image)
        raise
    _select_created_objects(context, objects)
    if pack_images:
        _report_build_progress(progress_callback, 0.92, "Packing terrain imagery")
        pack_collection_images(collection)
    _report_build_progress(
        progress_callback, 1.0, f"Created {len(objects)} terrain object(s)"
    )
    return tuple(objects)


def _report_build_progress(
    callback: Callable[[float, str], None] | None, progress: float, message: str
) -> None:
    if callback is not None:
        callback(progress, message)


def terrain_import_exists(import_id: str) -> bool:
    """Return whether the scene data already contains this terrain import."""

    return any(
        collection.get("blender_terrain_import_id") == import_id
        for collection in bpy.data.collections
    )


def collection_for_import(import_id: str) -> bpy.types.Collection | None:
    """Find the collection created for a persistent terrain import."""

    return next(
        (
            collection
            for collection in bpy.data.collections
            if collection.get("blender_terrain_import_id") == import_id
        ),
        None,
    )


def pack_collection_images(collection: bpy.types.Collection) -> tuple[bpy.types.Image, ...]:
    """Pack every external image used by mesh materials in one import collection."""

    images: dict[int, bpy.types.Image] = {}
    for object_ in collection.objects:
        if not isinstance(object_.data, bpy.types.Mesh):
            continue
        for material in object_.data.materials:
            if material is None or not material.use_nodes:
                continue
            for node in material.node_tree.nodes:
                if node.bl_idname == "ShaderNodeTexImage" and node.image is not None:
                    images[node.image.as_pointer()] = node.image
    for image in images.values():
        if image.packed_file is None:
            image.pack()
    return tuple(images.values())


def _create_mesh(
    name: str,
    vertices: Any,
    faces: Any,
    uv_shape: tuple[int, int] | None = None,
) -> bpy.types.Mesh:
    mesh = bpy.data.meshes.new(name)
    mesh.vertices.add(len(vertices))
    mesh.vertices.foreach_set("co", vertices.ravel())
    mesh.loops.add(faces.size)
    mesh.loops.foreach_set("vertex_index", faces.ravel())
    mesh.polygons.add(len(faces))
    mesh.polygons.foreach_set("loop_start", np.arange(len(faces), dtype=np.int32) * 4)
    mesh.polygons.foreach_set("loop_total", np.full(len(faces), 4, dtype=np.int32))
    mesh.update(calc_edges=True)
    if uv_shape is not None:
        width = float(vertices[:, 0].max())
        height = float(vertices[:, 1].max())
        if width <= 0.0 or height <= 0.0:
            raise RasterFormatError("Terrain mesh cannot create UVs for empty bounds")
        rows, columns = uv_shape
        if rows < 2 or columns < 2:
            raise RasterFormatError("Terrain heightmap requires at least two rows and columns")
        loop_vertices = faces.ravel()
        uv = np.column_stack(
            (
                _heightmap_uv_coordinates(
                    vertices[loop_vertices, 0], width, columns
                ),
                _heightmap_uv_coordinates(vertices[loop_vertices, 1], height, rows),
            )
        ).astype(np.float32, copy=False)
        uv_layer = mesh.uv_layers.new(name="TerrainUV")
        uv_layer.uv.foreach_set("vector", uv.ravel())
    return mesh


def _heightmap_uv_coordinates(
    positions: Any, extent: float, pixel_count: int
) -> Any:
    """Map geographic endpoints to legacy Blender image-texture sample centres."""

    normalized = positions / extent
    if pixel_count == 2:
        return (normalized * (pixel_count - 1) + 0.5) / pixel_count
    return (normalized * pixel_count - 0.5) / (pixel_count - 1)


def _create_heightmap_image(
    name: str, heightmap: np.ndarray[Any, np.dtype[np.float32]]
) -> bpy.types.Image:
    rows, columns = heightmap.shape
    image = bpy.data.images.new(
        name,
        width=columns,
        height=rows,
        alpha=False,
        float_buffer=True,
        is_data=True,
    )
    pixels = np.empty((rows, columns, 4), dtype=np.float32)
    south_up = np.flipud(heightmap)
    pixels[:, :, :3] = south_up[:, :, None]
    pixels[:, :, 3] = 1.0
    image.pixels.foreach_set(pixels.ravel())
    image.update()
    image.pack()
    return image


def _create_imagery_material(
    name: str,
    terrain_bounds: ProjectedBounds,
    imagery: tuple[_ImageryEntry, ...],
    import_id: str,
    marine_mask_image: bpy.types.Image | None = None,
) -> bpy.types.Material | None:
    coverage = tuple(
        (entry, transform)
        for entry in imagery
        if (transform := projected_texture_transform(terrain_bounds, entry.bounds)) is not None
    )
    if not coverage and marine_mask_image is None:
        return None
    if coverage and not bounds_fully_covered(
        terrain_bounds, tuple(entry.bounds for entry, _ in coverage)
    ):
        raise RasterFormatError("Imagery does not cover the complete terrain tile")

    material = bpy.data.materials.new(name)
    material["blender_terrain_import_id"] = import_id
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    links = material.node_tree.links
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    shader.inputs["Roughness"].default_value = 0.8
    coordinates = nodes.new("ShaderNodeTexCoord")
    color_socket = None
    for index, (entry, transform) in enumerate(coverage):
        mapping = nodes.new("ShaderNodeVectorMath")
        mapping.operation = "MULTIPLY_ADD"
        mapping.inputs[1].default_value = (transform.scale_u, transform.scale_v, 0.0)
        mapping.inputs[2].default_value = (transform.offset_u, transform.offset_v, 0.0)
        links.new(coordinates.outputs["Generated"], mapping.inputs[0])
        texture = nodes.new("ShaderNodeTexImage")
        texture.name = f"PNOA_{index:03d}"
        texture.image = bpy.data.images.load(str(entry.path), check_existing=True)
        texture.image.colorspace_settings.name = "sRGB"
        texture.extension = "CLIP"
        texture.interpolation = "Linear"
        links.new(mapping.outputs["Vector"], texture.inputs["Vector"])
        if color_socket is None:
            color_socket = texture.outputs["Color"]
        else:
            add = nodes.new("ShaderNodeMixRGB")
            add.blend_type = "MIX"
            links.new(color_socket, add.inputs[1])
            links.new(texture.outputs["Color"], add.inputs[2])
            links.new(texture.outputs["Alpha"], add.inputs[0])
            color_socket = add.outputs[0]
    if marine_mask_image is not None:
        marine_texture = nodes.new("ShaderNodeTexImage")
        marine_texture.name = "Marine_Mask"
        marine_texture.image = marine_mask_image
        marine_texture.extension = "EXTEND"
        marine_texture.interpolation = "Closest"
        links.new(coordinates.outputs["Generated"], marine_texture.inputs["Vector"])
        marine_mix = nodes.new("ShaderNodeMixRGB")
        marine_mix.name = "Land_Seabed_Mix"
        marine_mix.blend_type = "MIX"
        marine_mix.inputs[1].default_value = (0.8, 0.8, 0.8, 1.0)
        marine_mix.inputs[2].default_value = (0.18, 0.07, 0.025, 1.0)
        links.new(marine_texture.outputs["Color"], marine_mix.inputs[0])
        if color_socket is not None:
            links.new(color_socket, marine_mix.inputs[1])
        color_socket = marine_mix.outputs[0]
    if color_socket is not None:
        links.new(color_socket, shader.inputs["Base Color"])
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def _load_marine_mask(path: Path, expected_shape: tuple[int, ...]) -> np.ndarray:
    try:
        mask = np.load(path, mmap_mode="r", allow_pickle=False)
    except (OSError, ValueError) as exc:
        raise RasterFormatError("Cannot read processed marine mask") from exc
    if mask.dtype != np.uint8 or mask.shape != expected_shape:
        raise RasterFormatError("Processed marine mask does not match terrain tile")
    values = set(np.unique(mask).tolist())
    if not values <= {0, 1, 255}:
        raise RasterFormatError("Processed marine mask contains invalid values")
    return np.asarray(mask == 1, dtype=np.float32)


def _create_marine_mask_image(name: str, mask: np.ndarray) -> bpy.types.Image:
    image = _create_heightmap_image(name, mask)
    image.colorspace_settings.name = "Non-Color"
    return image


def _select_created_objects(
    context: bpy.types.Context, objects: list[bpy.types.Object]
) -> None:
    for selected in context.selected_objects:
        selected.select_set(False)
    for object_ in objects:
        object_.select_set(True)
    if objects:
        context.view_layer.objects.active = objects[0]


def _read_result(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RasterFormatError("Cannot read the completed delivery result") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 2
        or payload.get("state") not in {"COMPLETE", "COMPLETE_WITH_WARNINGS"}
    ):
        raise RasterFormatError("Delivery result is not complete")
    try:
        if not isinstance(payload["import_id"], str) or not isinstance(
            payload["task_id"], str
        ):
            raise TypeError
        UUID(payload["import_id"])
        UUID(payload["task_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RasterFormatError("Delivery result has invalid task or import identity") from exc
    return payload


def _parse_manifest(
    result: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[Any], list[Any]]:
    try:
        request = result["request"]
        provenance = result["provenance"]
        sources = result["sources"]
        crs = result["crs"]
        if (
            not isinstance(request, dict)
            or not isinstance(provenance, dict)
            or not isinstance(sources, list)
            or not sources
            or not isinstance(crs, list)
            or not crs
            or not isinstance(request["bounds_wgs84"], dict)
            or not isinstance(request["product"], str)
            or not isinstance(request["elevation_resolution_metres"], (int, float))
            or not isinstance(request["use_imagery"], bool)
            or not isinstance(provenance["source"], str)
            or not isinstance(provenance["data_policy_url"], str)
            or not isinstance(provenance["license"], str)
            or not isinstance(provenance["retrieved_at_utc"], str)
        ):
            raise TypeError
    except (KeyError, TypeError) as exc:
        raise RasterFormatError("Delivery result has incomplete provenance") from exc
    return request, provenance, sources, crs


def _parse_entry(
    entry: object,
) -> tuple[Path, ProjectedBounds, int, int, float, Path | None]:
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
        raw_mask_path = entry.get("marine_mask_path")
        marine_mask_path = (
            None if raw_mask_path is None else Path(raw_mask_path).resolve()
        )
        if raw_mask_path is not None and (
            not isinstance(raw_mask_path, str) or not marine_mask_path.is_file()
        ):
            raise ValueError
        if rows <= 0 or columns <= 0:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise RasterFormatError("Delivery result contains an invalid terrain tile") from exc
    return path, bounds, rows, columns, nodata, marine_mask_path


def _parse_imagery(entries: object) -> tuple[_ImageryEntry, ...]:
    if not isinstance(entries, list):
        raise RasterFormatError("Delivery result contains invalid terrain imagery")
    parsed: list[_ImageryEntry] = []
    for entry in entries:
        try:
            if not isinstance(entry, dict) or not isinstance(entry["bounds"], dict):
                raise TypeError
            raw_bounds = entry["bounds"]
            bounds = ProjectedBounds(
                float(raw_bounds["west"]), float(raw_bounds["south"]),
                float(raw_bounds["east"]), float(raw_bounds["north"]),
                int(raw_bounds["epsg"]),
            )
            path = Path(entry["path"]).resolve()
            if not path.is_file():
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise RasterFormatError("Delivery result contains invalid terrain imagery") from exc
        parsed.append(_ImageryEntry(path, bounds))
    return tuple(parsed)
