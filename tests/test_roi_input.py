from __future__ import annotations

import unittest

from blender_terrain.core import BBoxWGS84, bbox_from_center_size, format_bbox, parse_bbox
from blender_terrain.core.crs import ProjectedWorkArea, split_bbox_by_wgs84_utm_zone
from blender_terrain.core.projection import project_work_area_bounds
from blender_terrain.errors import BlenderTerrainError


class ROIInputTests(unittest.TestCase):
    def test_builds_metric_rectangle_around_projected_center(self) -> None:
        bounds = bbox_from_center_size(-3.7038, 40.4168, 2_000.0, 1_000.0)
        area = split_bbox_by_wgs84_utm_zone(bounds)[0]
        projected = project_work_area_bounds(ProjectedWorkArea(bounds, area.crs))

        self.assertAlmostEqual(projected.east - projected.west, 2_000.0, delta=0.01)
        self.assertAlmostEqual(projected.north - projected.south, 1_000.0, delta=0.01)
        self.assertLess(bounds.west, -3.7038)
        self.assertGreater(bounds.east, -3.7038)

    def test_supports_global_wgs84_utm_zones(self) -> None:
        bounds = bbox_from_center_size(-16.6291, 28.2916, 500.0, 500.0)

        self.assertEqual(split_bbox_by_wgs84_utm_zone(bounds)[0].crs.epsg, 32628)

    def test_rejects_invalid_dimensions_and_unsupported_centres(self) -> None:
        examples = (
            (-3.7, 40.4, 0.0, 100.0),
            (-3.7, 40.4, 100.0, -1.0),
            (20.0, 85.0, 100.0, 100.0),
        )
        for longitude, latitude, width, height in examples:
            with self.subTest(longitude=longitude), self.assertRaises(BlenderTerrainError):
                bbox_from_center_size(longitude, latitude, width, height)

    def test_formats_and_parses_clipboard_bbox(self) -> None:
        expected = BBoxWGS84(-0.39, 39.46, -0.37, 39.48)

        self.assertEqual(parse_bbox(format_bbox(expected)), expected)
        self.assertEqual(parse_bbox("-0.39 39.46 -0.37 39.48"), expected)

        with self.assertRaises(BlenderTerrainError):
            parse_bbox("-0.39, 39.46, missing")


if __name__ == "__main__":
    unittest.main()
