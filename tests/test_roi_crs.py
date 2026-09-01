from __future__ import annotations

import math
import unittest

from blender_terrain.core.crs import split_bbox_by_utm_zone, split_bbox_by_wgs84_utm_zone
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.errors import NoCoverageError, UserInputError


class BBoxWGS84Tests(unittest.TestCase):
    def test_rejects_non_finite_or_reversed_bounds(self) -> None:
        invalid_bounds = (
            (math.nan, 40.0, -3.0, 41.0),
            (-3.0, 40.0, -3.0, 41.0),
            (-3.0, 41.0, -2.0, 40.0),
            (-181.0, 40.0, -3.0, 41.0),
            (-3.0, -91.0, -2.0, 40.0),
        )
        for values in invalid_bounds:
            with self.subTest(values=values), self.assertRaises(UserInputError):
                BBoxWGS84(*values)

    def test_returns_a_closed_counterclockwise_ring(self) -> None:
        bounds = BBoxWGS84(-4.0, 39.0, -3.0, 40.0)

        self.assertEqual(bounds.polygon_ring()[0], bounds.polygon_ring()[-1])
        self.assertEqual(bounds.polygon_ring()[1], (-3.0, 39.0))


class UTMSelectionTests(unittest.TestCase):
    def test_selects_wgs84_utm_for_a_global_product_roi(self) -> None:
        work_areas = split_bbox_by_wgs84_utm_zone(
            BBoxWGS84(2.34, 48.85, 2.36, 48.87)
        )

        self.assertEqual(len(work_areas), 1)
        self.assertEqual(work_areas[0].crs.epsg, 32631)
        self.assertEqual(work_areas[0].crs.datum, "WGS84")

    def test_selects_native_crs_for_mainland_balearic_and_canary_bounds(self) -> None:
        examples = (
            (BBoxWGS84(-3.8, 40.3, -3.6, 40.5), 25830, "ETRS89"),
            (BBoxWGS84(2.5, 39.4, 2.8, 39.7), 25831, "ETRS89"),
            (BBoxWGS84(-16.7, 28.0, -16.4, 28.3), 4083, "REGCAN95"),
        )
        for bounds, epsg, datum in examples:
            with self.subTest(epsg=epsg):
                work_area = split_bbox_by_utm_zone(bounds)
                self.assertEqual(len(work_area), 1)
                self.assertEqual(work_area[0].crs.epsg, epsg)
                self.assertEqual(work_area[0].crs.datum, datum)

    def test_splits_bounds_exactly_at_a_zone_boundary(self) -> None:
        work_areas = split_bbox_by_utm_zone(BBoxWGS84(-0.2, 39.0, 0.3, 39.2))

        self.assertEqual([area.crs.utm_zone for area in work_areas], [30, 31])
        self.assertEqual(work_areas[0].bounds.east, 0.0)
        self.assertEqual(work_areas[1].bounds.west, 0.0)

    def test_rejects_longitudes_outside_supported_zones(self) -> None:
        with self.assertRaises(NoCoverageError):
            split_bbox_by_utm_zone(BBoxWGS84(8.0, 39.0, 9.0, 40.0))


if __name__ == "__main__":
    unittest.main()
