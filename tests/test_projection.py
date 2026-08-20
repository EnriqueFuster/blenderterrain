from __future__ import annotations

import unittest

from blender_terrain.core.crs import split_bbox_by_utm_zone
from blender_terrain.core.projection import project_wgs84_to_utm, project_work_area_bounds
from blender_terrain.core.roi import BBoxWGS84


class UTMProjectionTests(unittest.TestCase):
    def test_projects_known_points_with_expected_metre_values(self) -> None:
        examples = (
            (-3.7038, 40.4168, 440_290.458, 4_474_257.382, 25830),
            (2.6502, 39.5696, 469_954.524, 4_380_047.231, 25831),
            (-16.6291, 28.2916, 340_244.093, 3_130_581.718, 4083),
        )
        for longitude, latitude, expected_easting, expected_northing, epsg in examples:
            with self.subTest(epsg=epsg):
                area = split_bbox_by_utm_zone(
                    BBoxWGS84(
                        longitude - 0.001,
                        latitude - 0.001,
                        longitude + 0.001,
                        latitude + 0.001,
                    )
                )[0]
                point = project_wgs84_to_utm(longitude, latitude, area.crs)
                self.assertAlmostEqual(point.easting, expected_easting, delta=0.02)
                self.assertAlmostEqual(point.northing, expected_northing, delta=0.02)

    def test_projected_envelope_contains_all_bbox_corners(self) -> None:
        area = split_bbox_by_utm_zone(BBoxWGS84(-3.8, 40.3, -3.6, 40.5))[0]

        envelope = project_work_area_bounds(area)

        for longitude, latitude in area.bounds.polygon_ring():
            point = project_wgs84_to_utm(longitude, latitude, area.crs)
            self.assertLessEqual(envelope.west, point.easting)
            self.assertGreaterEqual(envelope.east, point.easting)
            self.assertLessEqual(envelope.south, point.northing)
            self.assertGreaterEqual(envelope.north, point.northing)


if __name__ == "__main__":
    unittest.main()
