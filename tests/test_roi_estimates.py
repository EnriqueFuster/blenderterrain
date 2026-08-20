from __future__ import annotations

import unittest

from blender_terrain.core import BBoxWGS84, estimate_bbox
from blender_terrain.errors import UserInputError


class ROIEstimateTests(unittest.TestCase):
    def test_estimates_a_small_valencia_bbox(self) -> None:
        estimate = estimate_bbox(BBoxWGS84(-0.39, 39.46, -0.37, 39.48))

        self.assertAlmostEqual(estimate.width_metres, 1716.0, delta=5.0)
        self.assertAlmostEqual(estimate.height_metres, 2223.9, delta=1.0)
        self.assertAlmostEqual(estimate.area_square_metres, 3_816_000.0, delta=20_000.0)
        self.assertEqual(estimate.sample_columns, 859)
        self.assertEqual(estimate.sample_rows, 1112)
        self.assertEqual(estimate.sample_count, 955_208)

    def test_sample_count_changes_with_resolution(self) -> None:
        bounds = BBoxWGS84(-3.71, 40.41, -3.70, 40.42)

        fine = estimate_bbox(bounds, resolution_metres=2.0)
        coarse = estimate_bbox(bounds, resolution_metres=4.0)

        self.assertGreater(fine.sample_count, coarse.sample_count)

    def test_rejects_invalid_resolution(self) -> None:
        with self.assertRaises(UserInputError):
            estimate_bbox(BBoxWGS84(-3.71, 40.41, -3.70, 40.42), 0.0)


if __name__ == "__main__":
    unittest.main()
