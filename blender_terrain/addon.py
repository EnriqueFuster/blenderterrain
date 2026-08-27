"""Central Blender class registration kept separate from portable modules."""

from __future__ import annotations

from collections.abc import Sequence

import bpy
from bpy.props import PointerProperty

from .ui import job_controller
from .ui.operators import (
    BLENDERTERRAIN_OT_apply_import_settings,
    BLENDERTERRAIN_OT_apply_selected_settings,
    BLENDERTERRAIN_OT_cancel_discovery,
    BLENDERTERRAIN_OT_copy_bbox,
    BLENDERTERRAIN_OT_create_terrain,
    BLENDERTERRAIN_OT_discover_sources,
    BLENDERTERRAIN_OT_download_data,
    BLENDERTERRAIN_OT_open_roi_map,
    BLENDERTERRAIN_OT_pack_imagery,
    BLENDERTERRAIN_OT_paste_bbox,
    BLENDERTERRAIN_OT_restore_selected_settings,
    BLENDERTERRAIN_OT_select_import_objects,
    BLENDERTERRAIN_OT_update_bbox_from_center,
    BLENDERTERRAIN_OT_validate_roi,
    shutdown_map_selector,
)
from .ui.panels import (
    BLENDERTERRAIN_PT_acquisition,
    BLENDERTERRAIN_PT_area,
    BLENDERTERRAIN_PT_creation,
    BLENDERTERRAIN_PT_data,
    BLENDERTERRAIN_PT_imported,
    BLENDERTERRAIN_PT_main,
)
from .ui.preferences import BLENDERTERRAIN_AddonPreferences
from .ui.properties import BLENDERTERRAIN_ROIProperties

_CLASSES: Sequence[type] = (
    BLENDERTERRAIN_AddonPreferences,
    BLENDERTERRAIN_ROIProperties,
    BLENDERTERRAIN_OT_update_bbox_from_center,
    BLENDERTERRAIN_OT_copy_bbox,
    BLENDERTERRAIN_OT_paste_bbox,
    BLENDERTERRAIN_OT_open_roi_map,
    BLENDERTERRAIN_OT_validate_roi,
    BLENDERTERRAIN_OT_discover_sources,
    BLENDERTERRAIN_OT_download_data,
    BLENDERTERRAIN_OT_create_terrain,
    BLENDERTERRAIN_OT_pack_imagery,
    BLENDERTERRAIN_OT_select_import_objects,
    BLENDERTERRAIN_OT_apply_import_settings,
    BLENDERTERRAIN_OT_apply_selected_settings,
    BLENDERTERRAIN_OT_restore_selected_settings,
    BLENDERTERRAIN_OT_cancel_discovery,
    BLENDERTERRAIN_PT_main,
    BLENDERTERRAIN_PT_area,
    BLENDERTERRAIN_PT_data,
    BLENDERTERRAIN_PT_acquisition,
    BLENDERTERRAIN_PT_creation,
    BLENDERTERRAIN_PT_imported,
)


def register(extension_package: str) -> None:
    """Register extension classes in dependency order."""

    BLENDERTERRAIN_AddonPreferences.bl_idname = extension_package
    job_controller.configure(extension_package)
    for class_type in _CLASSES:
        bpy.utils.register_class(class_type)
    bpy.types.Scene.blender_terrain_roi = PointerProperty(type=BLENDERTERRAIN_ROIProperties)
    job_controller.schedule_interrupted_job_recovery()


def unregister() -> None:
    """Unregister extension classes in reverse dependency order."""

    job_controller.shutdown()
    shutdown_map_selector()
    del bpy.types.Scene.blender_terrain_roi
    for class_type in reversed(_CLASSES):
        bpy.utils.unregister_class(class_type)


def registered_class_types() -> tuple[type, ...]:
    """Expose extension-owned class types for registration smoke tests."""

    return tuple(_CLASSES)
