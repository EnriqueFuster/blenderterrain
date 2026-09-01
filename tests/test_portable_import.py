"""Smoke tests for the portable package boundary."""

from __future__ import annotations

import ast
import tomllib
import unittest
from pathlib import Path


class PortableImportTests(unittest.TestCase):
    def test_package_imports_without_blender(self) -> None:
        import blender_terrain

        self.assertEqual(blender_terrain.__version__, "0.4.0")

    def test_public_version_matches_package_and_extension_metadata(self) -> None:
        import blender_terrain

        repository = Path(__file__).parents[1]
        project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))
        manifest = tomllib.loads(
            (repository / "blender_manifest.toml").read_text(encoding="utf-8")
        )

        self.assertEqual(project["project"]["version"], blender_terrain.__version__)
        self.assertEqual(manifest["version"], blender_terrain.__version__)

    def test_only_blender_boundary_modules_import_bpy(self) -> None:
        package_root = Path(__file__).parents[1] / "blender_terrain"
        allowed = {
            "addon.py",
            "ui/operators.py",
            "ui/job_controller.py",
            "ui/panels.py",
            "ui/preferences.py",
            "ui/properties.py",
            "ui/terrain_bake.py",
            "ui/terrain_builder.py",
            "ui/terrain_controls.py",
        }
        offenders: list[str] = []
        for path in package_root.rglob("*.py"):
            relative = path.relative_to(package_root).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports_bpy = any(
                (isinstance(node, ast.Import) and any(alias.name == "bpy" for alias in node.names))
                or (isinstance(node, ast.ImportFrom) and node.module == "bpy")
                for node in ast.walk(tree)
            )
            if imports_bpy and relative not in allowed:
                offenders.append(relative)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
