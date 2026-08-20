"""Verify an installed BlenderTerrain extension in an isolated Blender profile."""

from __future__ import annotations

import importlib

import addon_utils
import bpy


def main() -> None:
    """Check the enabled extension, then disable it and verify cleanup."""

    matching_modules = [
        module_name
        for module_name in bpy.context.preferences.addons.keys()  # noqa: SIM118
        if module_name.endswith(".blender_terrain")
    ]
    if len(matching_modules) != 1:
        raise RuntimeError(
            f"Expected one enabled BlenderTerrain extension, found {matching_modules!r}"
        )

    module_name = matching_modules[0]
    extension = importlib.import_module(module_name)
    classes = extension.blender_terrain.addon.registered_class_types()
    assert all(class_type.is_registered for class_type in classes)
    assert classes[0].bl_idname == module_name

    addon_utils.disable(module_name, default_set=False)
    assert not any(class_type.is_registered for class_type in classes)
    print(f"Installed extension smoke test passed: {module_name}")


if __name__ == "__main__":
    main()
