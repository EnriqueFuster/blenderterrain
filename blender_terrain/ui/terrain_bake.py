"""Bake an editable tiled terrain into one render-ready Blender object."""

from __future__ import annotations

import math

import bmesh
import bpy

from ..errors import UserInputError
from .terrain_builder import collection_for_import


def bake_and_merge_terrain(
    context: bpy.types.Context, import_id: str
) -> bpy.types.Object:
    """Evaluate and join one terrain import, then remove its editable sources."""

    source_collection = collection_for_import(import_id)
    if source_collection is None:
        raise UserInputError("Choose a terrain import first")
    sources = [
        object_
        for object_ in source_collection.objects
        if isinstance(object_.data, bpy.types.Mesh)
        and object_.get("blender_terrain_import_id") == import_id
    ]
    if not sources:
        raise UserInputError("The selected terrain import contains no mesh objects")
    epsg_codes = {object_.get("blender_terrain_epsg") for object_ in sources}
    if len(epsg_codes) != 1:
        raise UserInputError(
            "Terrain objects from different coordinate systems cannot be merged"
        )

    depsgraph = context.evaluated_depsgraph_get()
    short_id = import_id[:8]
    baked_collection = bpy.data.collections.new(f"BlenderTerrain_{short_id}_Baked")
    context.scene.collection.children.link(baked_collection)
    copies: list[bpy.types.Object] = []
    try:
        for source in sources:
            evaluated = source.evaluated_get(depsgraph)
            mesh = bpy.data.meshes.new_from_object(
                evaluated,
                preserve_all_data_layers=True,
                depsgraph=depsgraph,
            )
            copy = bpy.data.objects.new(f"{source.name}_Baked", mesh)
            copy.matrix_world = source.matrix_world.copy()
            baked_collection.objects.link(copy)
            copies.append(copy)

        for selected in context.selected_objects:
            selected.select_set(False)
        for copy in copies:
            copy.select_set(True)
        result = copies[0]
        context.view_layer.objects.active = result
        if len(copies) > 1 and bpy.ops.object.join() != {"FINISHED"}:
            raise UserInputError("Blender could not join the evaluated terrain meshes")

        result.name = f"BT_{short_id}_Baked"
        result.data.name = f"BT_{short_id}_BakedMesh"
        result["blender_terrain_representation"] = "BAKED"
        result["blender_terrain_source_import_id"] = import_id
        result["blender_terrain_epsg"] = next(iter(epsg_codes))
        resolution = float(
            source_collection.get("blender_terrain_elevation_resolution_metres", 1.0)
        )
        _weld_coincident_vertices(result, max(1.0e-6, resolution * 1.0e-4))

        for key, value in source_collection.items():
            baked_collection[key] = value
        baked_collection["blender_terrain_representation"] = "BAKED"
        baked_collection["blender_terrain_import_id"] = import_id
        baked_collection["blender_terrain_source_import_id"] = import_id
        baked_collection["blender_terrain_epsg"] = next(iter(epsg_codes))
        result["blender_terrain_import_id"] = import_id
        _remove_source_collection(source_collection)
        result.select_set(True)
        context.view_layer.objects.active = result
        return result
    except BaseException:
        for copy in tuple(baked_collection.objects):
            mesh = copy.data if isinstance(copy.data, bpy.types.Mesh) else None
            bpy.data.objects.remove(copy, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        bpy.data.collections.remove(baked_collection)
        raise


def _remove_source_collection(collection: bpy.types.Collection) -> None:
    meshes: dict[int, bpy.types.Mesh] = {}
    textures: dict[int, bpy.types.Texture] = {}
    images: dict[int, bpy.types.Image] = {}
    for object_ in tuple(collection.objects):
        if isinstance(object_.data, bpy.types.Mesh):
            meshes[object_.data.as_pointer()] = object_.data
        for modifier in object_.modifiers:
            if not isinstance(modifier, bpy.types.DisplaceModifier):
                continue
            texture = modifier.texture
            if texture is None:
                continue
            textures[texture.as_pointer()] = texture
            if texture.image is not None:
                images[texture.image.as_pointer()] = texture.image
        bpy.data.objects.remove(object_, do_unlink=True)
    bpy.data.collections.remove(collection)
    for mesh in meshes.values():
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    for texture in textures.values():
        if texture.users == 0:
            bpy.data.textures.remove(texture)
    for image in images.values():
        if image.users == 0:
            bpy.data.images.remove(image)


def _weld_coincident_vertices(object_: bpy.types.Object, tolerance: float) -> None:
    if not math.isfinite(tolerance) or tolerance <= 0.0:
        raise ValueError("Merge tolerance must be positive and finite")
    mesh = object_.data
    if not isinstance(mesh, bpy.types.Mesh):
        raise UserInputError("Baked terrain has no mesh data")
    editable = bmesh.new()
    try:
        editable.from_mesh(mesh)
        bmesh.ops.remove_doubles(editable, verts=editable.verts, dist=tolerance)
        editable.to_mesh(mesh)
        mesh.update()
    finally:
        editable.free()
