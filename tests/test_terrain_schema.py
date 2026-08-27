from __future__ import annotations

import unittest

from blender_terrain.core import (
    TERRAIN_SCHEMA_VERSION,
    TerrainRepresentation,
    TerrainSettings,
    read_terrain_metadata,
    subdivision_risk_message,
)
from blender_terrain.errors import RasterFormatError


class TerrainSchemaTests(unittest.TestCase):
    def test_interprets_unversioned_terrain_as_legacy_baked_geometry(self) -> None:
        metadata = read_terrain_metadata({"blender_terrain_vertical_scale": 2.0})

        self.assertTrue(metadata.legacy)
        self.assertEqual(metadata.schema_version, 1)
        self.assertEqual(metadata.representation, TerrainRepresentation.BAKED)
        self.assertEqual(metadata.settings.vertical_scale, 2.0)

    def test_reads_current_displacement_settings(self) -> None:
        metadata = read_terrain_metadata(
            {
                "blender_terrain_schema_version": TERRAIN_SCHEMA_VERSION,
                "blender_terrain_representation": "DISPLACEMENT",
                "blender_terrain_vertical_scale": 1.5,
                "blender_terrain_displacement_midlevel": 0.25,
                "blender_terrain_subdivision_viewport": 2,
                "blender_terrain_subdivision_render": 3,
                "blender_terrain_displacement_enabled": False,
            }
        )

        self.assertFalse(metadata.legacy)
        self.assertEqual(metadata.representation, TerrainRepresentation.DISPLACEMENT)
        self.assertEqual(metadata.settings.vertical_scale, 1.5)
        self.assertEqual(metadata.settings.displacement_midlevel, 0.25)
        self.assertEqual(metadata.settings.subdivision_viewport, 2)
        self.assertEqual(metadata.settings.subdivision_render, 3)
        self.assertFalse(metadata.settings.displacement_enabled)

    def test_rejects_unknown_versions_and_invalid_settings(self) -> None:
        examples = (
            {
                "blender_terrain_schema_version": 99,
                "blender_terrain_representation": "BAKED",
            },
            {
                "blender_terrain_schema_version": TERRAIN_SCHEMA_VERSION,
                "blender_terrain_representation": "UNKNOWN",
            },
            {
                "blender_terrain_schema_version": TERRAIN_SCHEMA_VERSION,
                "blender_terrain_representation": "DISPLACEMENT",
                "blender_terrain_vertical_scale": -1.0,
            },
            {
                "blender_terrain_schema_version": TERRAIN_SCHEMA_VERSION,
                "blender_terrain_representation": "DISPLACEMENT",
                "blender_terrain_displacement_midlevel": 1.1,
            },
        )
        for properties in examples:
            with self.subTest(properties=properties), self.assertRaises(RasterFormatError):
                read_terrain_metadata(properties)

    def test_uses_blenders_hard_subdivision_limit_and_reports_exponential_cost(self) -> None:
        self.assertEqual(TerrainSettings(subdivision_viewport=11).subdivision_viewport, 11)
        with self.assertRaises(ValueError):
            TerrainSettings(subdivision_render=12)
        with self.assertRaises(ValueError):
            TerrainSettings(displacement_midlevel=-0.1)

        self.assertIsNone(subdivision_risk_message(2, 2))
        self.assertIn("64 faces", subdivision_risk_message(3, 0) or "")
        self.assertIn("4,194,304 faces", subdivision_risk_message(0, 11) or "")


if __name__ == "__main__":
    unittest.main()
