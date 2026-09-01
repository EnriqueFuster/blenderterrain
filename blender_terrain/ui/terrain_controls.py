"""Post-import controls for displacement terrain collections."""

from __future__ import annotations

import math
from typing import Any

import bpy

from ..core import TerrainRepresentation, TerrainSettings, read_terrain_metadata
from ..errors import UserInputError
from .terrain_builder import (
    ensure_smooth_by_angle_modifier,
    get_smooth_angle,
    set_smooth_angle,
)


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
    properties.terrain_strength_multiplier = float(
        collection.get("blender_terrain_strength_multiplier", 1.0)
    )
    properties.terrain_displacement_midlevel = metadata.settings.displacement_midlevel
    properties.terrain_subdivision_viewport = metadata.settings.subdivision_viewport
    properties.terrain_subdivision_render = metadata.settings.subdivision_render
    properties.terrain_displacement_enabled = metadata.settings.displacement_enabled
    properties.terrain_smooth_angle = float(
        collection.get("blender_terrain_smooth_angle", 0.0)
    )


def apply_global_settings(properties: Any) -> int:
    """Apply shared scale and subdivision settings to one displacement import."""

    collection = _required_displacement_collection(properties.active_import_id)
    settings = TerrainSettings(
        vertical_scale=properties.terrain_vertical_scale,
        displacement_midlevel=properties.terrain_displacement_midlevel,
        subdivision_viewport=properties.terrain_subdivision_viewport,
        subdivision_render=properties.terrain_subdivision_render,
        displacement_enabled=properties.terrain_displacement_enabled,
    )
    collection["blender_terrain_vertical_scale"] = settings.vertical_scale
    multiplier = float(properties.terrain_strength_multiplier)
    if not math.isfinite(multiplier) or multiplier < 0.0:
        raise UserInputError("Strength multiplier must be a non-negative finite value")
    collection["blender_terrain_strength_multiplier"] = multiplier
    collection["blender_terrain_displacement_midlevel"] = settings.displacement_midlevel
    collection["blender_terrain_subdivision_viewport"] = settings.subdivision_viewport
    collection["blender_terrain_subdivision_render"] = settings.subdivision_render
    collection["blender_terrain_displacement_enabled"] = settings.displacement_enabled
    smooth_angle = _validated_smooth_angle(properties.terrain_smooth_angle)
    collection["blender_terrain_smooth_angle"] = smooth_angle
    objects = _terrain_objects(collection)
    for object_ in objects:
        object_.scale.z = settings.vertical_scale
        subdivision, displacement, smooth = _modifiers(object_)
        elevation_range = float(object_["blender_terrain_elevation_range"])
        displacement.strength = elevation_range * multiplier
        displacement.mid_level = settings.displacement_midlevel
        subdivision.levels = settings.subdivision_viewport
        subdivision.render_levels = settings.subdivision_render
        displacement.show_viewport = settings.displacement_enabled
        displacement.show_render = settings.displacement_enabled
        _apply_smoothing(smooth, smooth_angle)
        object_["blender_terrain_strength_multiplier"] = multiplier
        object_["blender_terrain_displacement_midlevel"] = settings.displacement_midlevel
        object_["blender_terrain_subdivision_viewport"] = settings.subdivision_viewport
        object_["blender_terrain_subdivision_render"] = settings.subdivision_render
        object_["blender_terrain_smooth_angle"] = smooth_angle
    return len(objects)


def apply_selected_settings(context: bpy.types.Context, properties: Any) -> tuple[int, int]:
    """Apply local strength and subdivision overrides to selected terrain objects."""

    collection = _required_displacement_collection(properties.active_import_id)
    selected = _selected_terrain_objects(context, collection)
    if not selected:
        raise UserInputError("Select at least one object from the active terrain import")
    multiplier = float(properties.selected_strength_multiplier)
    midlevel = float(properties.selected_displacement_midlevel)
    if not math.isfinite(midlevel) or not 0.0 <= midlevel <= 1.0:
        raise UserInputError("Midlevel must be between zero and one")
    viewport = int(properties.selected_subdivision_viewport)
    render = int(properties.selected_subdivision_render)
    smooth_angle = _validated_smooth_angle(properties.selected_smooth_angle)
    for object_ in selected:
        subdivision, displacement, smooth = _modifiers(object_)
        elevation_range = float(object_["blender_terrain_elevation_range"])
        displacement.strength = elevation_range * multiplier
        displacement.mid_level = midlevel
        subdivision.levels = viewport
        subdivision.render_levels = render
        _apply_smoothing(smooth, smooth_angle)
        object_["blender_terrain_strength_multiplier"] = multiplier
        object_["blender_terrain_displacement_midlevel"] = midlevel
        object_["blender_terrain_subdivision_viewport"] = viewport
        object_["blender_terrain_subdivision_render"] = render
        object_["blender_terrain_smooth_angle"] = smooth_angle
    return len(selected), count_strength_seams(collection)


def restore_selected_settings(context: bpy.types.Context, properties: Any) -> int:
    """Restore selected terrain objects to their import-wide settings."""

    collection = _required_displacement_collection(properties.active_import_id)
    selected = _selected_terrain_objects(context, collection)
    if not selected:
        raise UserInputError("Select at least one object from the active terrain import")
    metadata = read_terrain_metadata(dict(collection.items()))
    multiplier = float(collection.get("blender_terrain_strength_multiplier", 1.0))
    midlevel = metadata.settings.displacement_midlevel
    smooth_angle = float(
        collection.get("blender_terrain_smooth_angle", 0.0)
    )
    for object_ in selected:
        subdivision, displacement, smooth = _modifiers(object_)
        displacement.strength = float(object_["blender_terrain_elevation_range"]) * multiplier
        displacement.mid_level = midlevel
        subdivision.levels = metadata.settings.subdivision_viewport
        subdivision.render_levels = metadata.settings.subdivision_render
        _apply_smoothing(smooth, smooth_angle)
        object_["blender_terrain_strength_multiplier"] = multiplier
        object_["blender_terrain_displacement_midlevel"] = midlevel
        object_["blender_terrain_subdivision_viewport"] = (
            metadata.settings.subdivision_viewport
        )
        object_["blender_terrain_subdivision_render"] = metadata.settings.subdivision_render
        object_["blender_terrain_smooth_angle"] = smooth_angle
    return len(selected)


def sync_selected_settings(
    context: bpy.types.Context, properties: Any, *, force: bool = False
) -> bool:
    """Load actual modifier values from the active or last selected terrain object."""

    collection = _collection(properties.active_import_id)
    if collection is None:
        properties.selected_object_name = ""
        properties.selected_objects_signature = ""
        return False
    selected = _selected_terrain_objects(context, collection)
    active = context.view_layer.objects.active
    source = active if active in selected else (selected[-1] if selected else None)
    signature = "|".join(str(object_.as_pointer()) for object_ in selected)
    signature += f":{0 if source is None else source.as_pointer()}"
    if not force and signature == properties.selected_objects_signature:
        return source is not None
    properties.selected_objects_signature = signature
    if source is None:
        properties.selected_object_name = ""
        return False
    subdivision, displacement, smooth = _modifiers(source)
    elevation_range = float(source["blender_terrain_elevation_range"])
    multiplier = displacement.strength / elevation_range if elevation_range else 0.0
    properties.selected_strength_multiplier = multiplier
    properties.selected_displacement_midlevel = displacement.mid_level
    properties.selected_subdivision_viewport = subdivision.levels
    properties.selected_subdivision_render = subdivision.render_levels
    properties.selected_smooth_angle = get_smooth_angle(smooth)
    properties.selected_object_name = source.name
    return True


def has_selected_terrain_objects(context: bpy.types.Context, import_id: str) -> bool:
    """Return whether the active import contains a selected terrain object."""

    collection = _collection(import_id)
    return collection is not None and bool(
        _selected_terrain_objects(context, collection)
    )


def request_selected_settings_sync() -> None:
    """Schedule selection-to-panel synchronization outside the panel draw cycle."""

    if not bpy.app.timers.is_registered(_sync_selected_settings_on_timer):
        bpy.app.timers.register(_sync_selected_settings_on_timer, first_interval=0.0)


def _sync_selected_settings_on_timer() -> None:
    scene = getattr(bpy.context, "scene", None)
    if scene is None or not hasattr(scene, "blender_terrain_roi"):
        return None
    properties = scene.blender_terrain_roi
    sync_selected_settings(bpy.context, properties)
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    return None


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
) -> tuple[bpy.types.SubsurfModifier, bpy.types.DisplaceModifier, Any]:
    subdivision = object_.modifiers.get("Terrain Subdivision")
    displacement = object_.modifiers.get("Terrain Displacement")
    smooth = ensure_smooth_by_angle_modifier(object_)
    if not isinstance(subdivision, bpy.types.SubsurfModifier) or not isinstance(
        displacement, bpy.types.DisplaceModifier
    ):
        raise UserInputError(f"Terrain object has missing modifiers: {object_.name}")
    return subdivision, displacement, smooth


def _validated_smooth_angle(value: float) -> float:
    angle = float(value)
    if not math.isfinite(angle) or not 0.0 <= angle <= math.pi:
        raise UserInputError("Smooth angle must be between 0 and 180 degrees")
    return angle


def _apply_smoothing(modifier: Any, angle: float) -> None:
    modifier.show_viewport = True
    modifier.show_render = True
    set_smooth_angle(modifier, angle)


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
