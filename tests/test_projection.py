from __future__ import annotations

import unittest

import numpy as np

from blender_terrain.core.crs import LAMBERT93, work_area_for_crs
from blender_terrain.core.projection import (
    project_arrays_to_wgs84,
    project_to_wgs84,
    project_utm_arrays_to_wgs84,
    project_utm_to_wgs84,
    project_wgs84,
    project_wgs84_to_utm,
    project_work_area_bounds,
)
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.providers.spain_crs import (
    split_spain_bbox_by_utm_zone as split_bbox_by_utm_zone,
)


class UTMProjectionTests(unittest.TestCase):
    def test_array_inverse_matches_scalar_inverse(self) -> None:
        area = split_bbox_by_utm_zone(BBoxWGS84(-3.8, 40.3, -3.6, 40.5))[0]
        points = (
            project_wgs84_to_utm(-3.8, 40.3, area.crs),
            project_wgs84_to_utm(-3.6, 40.5, area.crs),
        )

        longitude, latitude = project_utm_arrays_to_wgs84(
            np.asarray([point.easting for point in points]),
            np.asarray([point.northing for point in points]),
            area.crs,
        )

        np.testing.assert_allclose(longitude, [-3.8, -3.6], atol=1e-8)
        np.testing.assert_allclose(latitude, [40.3, 40.5], atol=1e-8)

    def test_round_trips_supported_geographic_points(self) -> None:
        points = ((-3.7038, 40.4168), (2.6502, 39.5696), (-16.6291, 28.2916))
        for longitude, latitude in points:
            area = split_bbox_by_utm_zone(
                BBoxWGS84(
                    longitude - 0.001,
                    latitude - 0.001,
                    longitude + 0.001,
                    latitude + 0.001,
                )
            )[0]
            projected = project_wgs84_to_utm(longitude, latitude, area.crs)

            recovered = project_utm_to_wgs84(projected, area.crs)

            self.assertAlmostEqual(recovered.longitude, longitude, places=8)
            self.assertAlmostEqual(recovered.latitude, latitude, places=8)
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


class Lambert93ProjectionTests(unittest.TestCase):
    def test_projection_origin_maps_to_false_origin(self) -> None:
        point = project_wgs84(3.0, 46.5, LAMBERT93)

        self.assertAlmostEqual(point.easting, 700_000.0, delta=0.001)
        self.assertAlmostEqual(point.northing, 6_600_000.0, delta=0.001)

    def test_round_trips_points_across_metropolitan_france_and_corsica(self) -> None:
        points = ((-4.5, 48.4), (2.35, 48.86), (7.26, 43.7), (9.1, 42.0))
        for longitude, latitude in points:
            point = project_wgs84(longitude, latitude, LAMBERT93)
            recovered = project_to_wgs84(point, LAMBERT93)

            with self.subTest(longitude=longitude, latitude=latitude):
                self.assertAlmostEqual(recovered.longitude, longitude, places=9)
                self.assertAlmostEqual(recovered.latitude, latitude, places=9)

    def test_array_inverse_matches_scalar_projection(self) -> None:
        points = tuple(
            project_wgs84(x, y, LAMBERT93) for x, y in ((2.3, 48.8), (7.2, 43.6))
        )

        longitude, latitude = project_arrays_to_wgs84(
            np.asarray([point.easting for point in points]),
            np.asarray([point.northing for point in points]),
            LAMBERT93,
        )

        np.testing.assert_allclose(longitude, [2.3, 7.2], atol=1e-9)
        np.testing.assert_allclose(latitude, [48.8, 43.6], atol=1e-9)

    def test_projected_envelope_contains_bbox_edges(self) -> None:
        area = work_area_for_crs(BBoxWGS84(2.2, 48.7, 2.5, 48.95), 2154)
        envelope = project_work_area_bounds(area)

        for longitude, latitude in area.bounds.polygon_ring():
            point = project_wgs84(longitude, latitude, area.crs)
            self.assertLessEqual(envelope.west, point.easting)
            self.assertGreaterEqual(envelope.east, point.easting)
            self.assertLessEqual(envelope.south, point.northing)
            self.assertGreaterEqual(envelope.north, point.northing)


if __name__ == "__main__":
    unittest.main()
