"""Smoke tests for the portable package boundary."""

from __future__ import annotations

import unittest


class PortableImportTests(unittest.TestCase):
    def test_package_imports_without_blender(self) -> None:
        import blender_terrain

        self.assertEqual(blender_terrain.__version__, "0.0.0")


if __name__ == "__main__":
    unittest.main()

