"""Register and unregister the source extension in factory-startup Blender."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


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
        classes = addon.registered_class_types()
        assert all(class_type.is_registered for class_type in classes)
        assert classes[0].bl_idname == repository.name
        extension.unregister()
        assert not any(class_type.is_registered for class_type in classes)
    print("BlenderTerrain register/unregister smoke test passed")


if __name__ == "__main__":
    main()
