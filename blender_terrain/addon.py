"""Central Blender class registration kept separate from portable modules."""

from __future__ import annotations

from collections.abc import Sequence

import bpy

from .ui.panels import BLENDERTERRAIN_PT_main
from .ui.preferences import BLENDERTERRAIN_AddonPreferences

_CLASSES: Sequence[type] = (
    BLENDERTERRAIN_AddonPreferences,
    BLENDERTERRAIN_PT_main,
)


def register(extension_package: str) -> None:
    """Register extension classes in dependency order."""

    BLENDERTERRAIN_AddonPreferences.bl_idname = extension_package
    for class_type in _CLASSES:
        bpy.utils.register_class(class_type)


def unregister() -> None:
    """Unregister extension classes in reverse dependency order."""

    for class_type in reversed(_CLASSES):
        bpy.utils.unregister_class(class_type)


def registered_class_types() -> tuple[type, ...]:
    """Expose extension-owned class types for registration smoke tests."""

    return tuple(_CLASSES)
