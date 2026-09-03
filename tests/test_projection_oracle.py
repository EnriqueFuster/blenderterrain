from __future__ import annotations

import unittest

from blender_terrain.core.crs import LAMBERT93
from blender_terrain.core.projection import project_wgs84, project_wgs84_to_utm
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.providers.spain_crs import (
    split_spain_bbox_by_utm_zone as split_bbox_by_utm_zone,
)

try:
    from pyproj import Transformer
except ImportError:
    Transformer = None  # type: ignore[assignment,misc]


@unittest.skipUnless(Transformer is not None, "pyproj oracle is not installed")
class ProjectionOracleTests(unittest.TestCase):
    def test_matches_proj_across_supported_span(self) -> None:
        points = (
            (-9.1, 42.5),
            (-3.7, 40.4),
            (-0.1, 39.5),
            (2.6, 39.6),
            (-17.8, 28.6),
            (-13.5, 28.1),
        )
        for longitude, latitude in points:
            area = split_bbox_by_utm_zone(
                BBoxWGS84(
                    longitude - 0.001,
                    latitude - 0.001,
                    longitude + 0.001,
                    latitude + 0.001,
                )
            )[0]
            assert Transformer is not None
            transformer = Transformer.from_crs(4326, area.crs.epsg, always_xy=True)
            expected_easting, expected_northing = transformer.transform(longitude, latitude)

            actual = project_wgs84_to_utm(longitude, latitude, area.crs)

            with self.subTest(epsg=area.crs.epsg, longitude=longitude):
                self.assertAlmostEqual(actual.easting, expected_easting, delta=0.01)
                self.assertAlmostEqual(actual.northing, expected_northing, delta=0.01)

    def test_lambert93_matches_proj_across_supported_extent(self) -> None:
        assert Transformer is not None
        transformer = Transformer.from_crs(4326, 2154, always_xy=True)
        points = ((-4.5, 48.4), (2.35, 48.86), (7.26, 43.7), (9.1, 42.0))
        for longitude, latitude in points:
            expected_easting, expected_northing = transformer.transform(longitude, latitude)
            actual = project_wgs84(longitude, latitude, LAMBERT93)

            with self.subTest(longitude=longitude, latitude=latitude):
                self.assertAlmostEqual(actual.easting, expected_easting, delta=0.02)
                self.assertAlmostEqual(actual.northing, expected_northing, delta=0.02)


if __name__ == "__main__":
    unittest.main()
