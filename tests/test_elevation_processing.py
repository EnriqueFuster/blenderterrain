from __future__ import annotations

import unittest

import numpy as np

from blender_terrain.core.elevation_processing import _bilinear_resample
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


if __name__ == "__main__":
    unittest.main()
