from __future__ import annotations

import unittest

import numpy as np

from blender_terrain.core.crs import CRSInfo
from blender_terrain.core.elevation_processing import _bilinear_resample, _mask_outside_region
from blender_terrain.core.grid import GridTile
from blender_terrain.core.projection import project_wgs84_to_utm
from blender_terrain.core.roi import PolygonWGS84, RegionOfInterest, closed_ring
from blender_terrain.models import ProjectedBounds


class BilinearElevationTests(unittest.TestCase):
    def test_preserves_values_when_source_and_target_centres_match(self) -> None:
        source = np.array([[0.0, 10.0], [20.0, 30.0]], dtype=np.float32)

        result = _bilinear_resample(
            source, 0.0, 2.0, 1.0, ProjectedBounds(0, 0, 2, 2, 25830),
            2, 2, -9999.0,
        )

        np.testing.assert_array_equal(result, source)

    def test_interpolates_at_the_centre_of_four_source_cells(self) -> None:
        source = np.array([[0.0, 10.0], [20.0, 30.0]], dtype=np.float32)

        result = _bilinear_resample(
            source, 0.0, 2.0, 1.0, ProjectedBounds(0, 0, 2, 2, 25830),
            1, 1, -9999.0,
        )

        self.assertEqual(result[0, 0], 15.0)

    def test_renormalizes_weights_around_nodata(self) -> None:
        source = np.array([[-9999.0, 10.0], [20.0, 30.0]], dtype=np.float32)

        result = _bilinear_resample(
            source, 0.0, 2.0, 1.0, ProjectedBounds(0, 0, 2, 2, 25830),
            1, 1, -9999.0,
        )

        self.assertEqual(result[0, 0], 20.0)

    def test_polygon_mask_keeps_inside_nodes_and_removes_bbox_corners(self) -> None:
        crs = CRSInfo(25830, "ETRS89 / UTM zone 30N", "ETRS89", 30)
        region = RegionOfInterest(
            (
                PolygonWGS84(
                    closed_ring(((-3.1, 40.0), (-2.9, 40.0), (-3.0, 40.2)))
                ),
            )
        )
        projected = [
            project_wgs84_to_utm(longitude, latitude, crs)
            for longitude, latitude in region.polygons[0].exterior
        ]
        bounds = ProjectedBounds(
            min(point.easting for point in projected),
            min(point.northing for point in projected),
            max(point.easting for point in projected),
            max(point.northing for point in projected),
            crs.epsg,
        )
        tile = GridTile(0, 0, 0, 0, 20, 20, bounds)
        data = np.ones((21, 21), dtype=np.float32)

        _mask_outside_region(data, tile, -9999.0, region, crs)

        self.assertTrue(np.any(data == 1.0))
        self.assertTrue(np.any(data == -9999.0))
        self.assertEqual(data[0, 0], -9999.0)
        self.assertEqual(data[0, -1], -9999.0)


if __name__ == "__main__":
    unittest.main()
