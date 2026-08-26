"""Central Blender class registration kept separate from portable modules."""

from __future__ import annotations

from collections.abc import Sequence

import bpy
from bpy.props import PointerProperty

from .ui import job_controller
from .ui.operators import (
    BLENDERTERRAIN_OT_cancel_discovery,
    BLENDERTERRAIN_OT_create_terrain,
    BLENDERTERRAIN_OT_discover_sources,
    BLENDERTERRAIN_OT_download_data,
    BLENDERTERRAIN_OT_pack_imagery,
    BLENDERTERRAIN_OT_validate_roi,
)
from .ui.panels import BLENDERTERRAIN_PT_main
from .ui.preferences import BLENDERTERRAIN_AddonPreferences
from .ui.properties import BLENDERTERRAIN_ROIProperties

_CLASSES: Sequence[type] = (
    BLENDERTERRAIN_AddonPreferences,
    BLENDERTERRAIN_ROIProperties,
    BLENDERTERRAIN_OT_validate_roi,
    BLENDERTERRAIN_OT_discover_sources,
    BLENDERTERRAIN_OT_download_data,
    BLENDERTERRAIN_OT_create_terrain,
    BLENDERTERRAIN_OT_pack_imagery,
    BLENDERTERRAIN_OT_cancel_discovery,
    BLENDERTERRAIN_PT_main,
)


def register(extension_package: str) -> None:
    """Register extension classes in dependency order."""

    BLENDERTERRAIN_AddonPreferences.bl_idname = extension_package
    job_controller.configure(extension_package)
    for class_type in _CLASSES:
        bpy.utils.register_class(class_type)
    bpy.types.Scene.blender_terrain_roi = PointerProperty(type=BLENDERTERRAIN_ROIProperties)


def unregister() -> None:
    """Unregister extension classes in reverse dependency order."""

    job_controller.shutdown()
    del bpy.types.Scene.blender_terrain_roi
    for class_type in reversed(_CLASSES):
        bpy.utils.unregister_class(class_type)


def registered_class_types() -> tuple[type, ...]:
    """Expose extension-owned class types for registration smoke tests."""

    return tuple(_CLASSES)
