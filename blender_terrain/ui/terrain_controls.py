"""Post-import controls for displacement terrain collections."""

from __future__ import annotations

import math
from typing import Any

import bpy

from ..core import TerrainRepresentation, TerrainSettings, read_terrain_metadata
from ..errors import UserInputError


def import_items() -> list[tuple[str, str, str]]:
    """Return UI choices for every BlenderTerrain collection in the file."""

    items: list[tuple[str, str, str]] = []
    for collection in bpy.data.collections:
        import_id = collection.get("blender_terrain_import_id")
        if not isinstance(import_id, str):
            continue
        product = str(collection.get("blender_terrain_product", "Terrain"))
        tile_count = sum(
            1
            for object_ in collection.objects
            if isinstance(object_.data, bpy.types.Mesh)
        )
        items.append(
            (
                import_id,
                f"{collection.name} · {product} · {tile_count} object(s)",
                f"Edit terrain import {import_id}",
            )
        )
    return items


def load_import_settings(properties: Any) -> None:
    """Copy persistent collection settings into editable scene controls."""

    collection = _collection(properties.active_import_id)
    if collection is None:
        properties.active_import_representation = ""
        properties.active_import_full_resolution_mesh = False
        return
    metadata = read_terrain_metadata(dict(collection.items()))
    properties.active_import_representation = metadata.representation.value
    properties.active_import_full_resolution_mesh = bool(
        collection.get("blender_terrain_full_resolution_mesh", False)
    )
    properties.terrain_vertical_scale = metadata.settings.vertical_scale
    properties.terrain_subdivision_viewport = metadata.settings.subdivision_viewport
    properties.terrain_subdivision_render = metadata.settings.subdivision_render
    properties.terrain_displacement_enabled = metadata.settings.displacement_enabled


def apply_global_settings(properties: Any) -> int:
    """Apply shared scale and subdivision settings to one displacement import."""

    collection = _required_displacement_collection(properties.active_import_id)
    settings = TerrainSettings(
        vertical_scale=properties.terrain_vertical_scale,
        subdivision_viewport=properties.terrain_subdivision_viewport,
        subdivision_render=properties.terrain_subdivision_render,
        displacement_enabled=properties.terrain_displacement_enabled,
    )
    collection["blender_terrain_vertical_scale"] = settings.vertical_scale
    collection["blender_terrain_subdivision_viewport"] = settings.subdivision_viewport
    collection["blender_terrain_subdivision_render"] = settings.subdivision_render
    collection["blender_terrain_displacement_enabled"] = settings.displacement_enabled
    objects = _terrain_objects(collection)
    for object_ in objects:
        object_.scale.z = settings.vertical_scale
        subdivision, displacement = _modifiers(object_)
        subdivision.levels = settings.subdivision_viewport
        subdivision.render_levels = settings.subdivision_render
        displacement.show_viewport = settings.displacement_enabled
        displacement.show_render = settings.displacement_enabled
    return len(objects)


def apply_selected_settings(context: bpy.types.Context, properties: Any) -> tuple[int, int]:
    """Apply local strength and subdivision overrides to selected terrain objects."""

    collection = _required_displacement_collection(properties.active_import_id)
    selected = _selected_terrain_objects(context, collection)
    if not selected:
        raise UserInputError("Select at least one object from the active terrain import")
    multiplier = float(properties.selected_strength_multiplier)
    viewport = int(properties.selected_subdivision_viewport)
    render = int(properties.selected_subdivision_render)
    for object_ in selected:
        subdivision, displacement = _modifiers(object_)
        elevation_range = float(object_["blender_terrain_elevation_range"])
        displacement.strength = elevation_range * multiplier
        subdivision.levels = viewport
        subdivision.render_levels = render
        object_["blender_terrain_strength_multiplier"] = multiplier
        object_["blender_terrain_subdivision_viewport"] = viewport
        object_["blender_terrain_subdivision_render"] = render
    return len(selected), count_strength_seams(collection)


def restore_selected_settings(context: bpy.types.Context, properties: Any) -> int:
    """Restore selected terrain objects to their import-wide settings."""

    collection = _required_displacement_collection(properties.active_import_id)
    selected = _selected_terrain_objects(context, collection)
    if not selected:
        raise UserInputError("Select at least one object from the active terrain import")
    metadata = read_terrain_metadata(dict(collection.items()))
    for object_ in selected:
        subdivision, displacement = _modifiers(object_)
        displacement.strength = float(object_["blender_terrain_elevation_range"])
        subdivision.levels = metadata.settings.subdivision_viewport
        subdivision.render_levels = metadata.settings.subdivision_render
        object_["blender_terrain_strength_multiplier"] = 1.0
        object_["blender_terrain_subdivision_viewport"] = (
            metadata.settings.subdivision_viewport
        )
        object_["blender_terrain_subdivision_render"] = metadata.settings.subdivision_render
    return len(selected)


def select_import_objects(context: bpy.types.Context, import_id: str) -> int:
    """Select every mesh object owned by one terrain import."""

    collection = _collection(import_id)
    if collection is None:
        raise UserInputError("The selected terrain import no longer exists")
    for object_ in context.selected_objects:
        object_.select_set(False)
    objects = _terrain_objects(collection)
    for object_ in objects:
        object_.select_set(True)
    if objects:
        context.view_layer.objects.active = objects[0]
    return len(objects)


def count_strength_seams(collection: bpy.types.Collection) -> int:
    """Count shared tile edges whose displacement multipliers differ."""

    objects = _terrain_objects(collection)
    seams = 0
    for index, first in enumerate(objects):
        for second in objects[index + 1 :]:
            if _share_edge(first, second) and not math.isclose(
                float(first.get("blender_terrain_strength_multiplier", 1.0)),
                float(second.get("blender_terrain_strength_multiplier", 1.0)),
            ):
                seams += 1
    return seams


def _collection(import_id: str) -> bpy.types.Collection | None:
    return next(
        (
            collection
            for collection in bpy.data.collections
            if collection.get("blender_terrain_import_id") == import_id
        ),
        None,
    )


def _required_displacement_collection(import_id: str) -> bpy.types.Collection:
    collection = _collection(import_id)
    if collection is None:
        raise UserInputError("Choose a terrain import first")
    metadata = read_terrain_metadata(dict(collection.items()))
    if metadata.representation is not TerrainRepresentation.DISPLACEMENT:
        raise UserInputError("Legacy baked terrains do not have displacement controls")
    return collection


def _terrain_objects(collection: bpy.types.Collection) -> list[bpy.types.Object]:
    return [
        object_
        for object_ in collection.objects
        if isinstance(object_.data, bpy.types.Mesh)
        and object_.get("blender_terrain_import_id")
        == collection.get("blender_terrain_import_id")
    ]


def _selected_terrain_objects(
    context: bpy.types.Context, collection: bpy.types.Collection
) -> list[bpy.types.Object]:
    allowed = {object_.as_pointer() for object_ in _terrain_objects(collection)}
    return [object_ for object_ in context.selected_objects if object_.as_pointer() in allowed]


def _modifiers(
    object_: bpy.types.Object,
) -> tuple[bpy.types.SubsurfModifier, bpy.types.DisplaceModifier]:
    subdivision = object_.modifiers.get("Terrain Subdivision")
    displacement = object_.modifiers.get("Terrain Displacement")
    if not isinstance(subdivision, bpy.types.SubsurfModifier) or not isinstance(
        displacement, bpy.types.DisplaceModifier
    ):
        raise UserInputError(f"Terrain object has missing modifiers: {object_.name}")
    return subdivision, displacement


def _share_edge(first: bpy.types.Object, second: bpy.types.Object) -> bool:
    if first.get("blender_terrain_epsg") != second.get("blender_terrain_epsg"):
        return False
    fw, fs, fe, fn = _bounds(first)
    sw, ss, se, sn = _bounds(second)
    vertical = (math.isclose(fe, sw) or math.isclose(se, fw)) and min(fn, sn) > max(fs, ss)
    horizontal = (math.isclose(fn, ss) or math.isclose(sn, fs)) and min(fe, se) > max(fw, sw)
    return vertical or horizontal


def _bounds(object_: bpy.types.Object) -> tuple[float, float, float, float]:
    try:
        return (
            float(object_["blender_terrain_west"]),
            float(object_["blender_terrain_south"]),
            float(object_["blender_terrain_east"]),
            float(object_["blender_terrain_north"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise UserInputError(f"Terrain object has invalid bounds: {object_.name}") from exc
