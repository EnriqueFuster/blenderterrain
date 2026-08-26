from __future__ import annotations

import unittest

import numpy as np

from blender_terrain.core import (
    ElevationRange,
    calculate_elevation_range,
    normalize_heightmap,
)
from blender_terrain.errors import RasterFormatError


class HeightmapTests(unittest.TestCase):
    def test_uses_one_range_across_tiles_and_preserves_shared_edges(self) -> None:
        west = np.array([[100.0, 110.0], [120.0, 130.0]], dtype=np.float32)
        east = np.array([[110.0, 140.0], [130.0, 150.0]], dtype=np.float32)

        shared = calculate_elevation_range(((west, -9999.0), (east, -9999.0)))
        west_heightmap = normalize_heightmap(west, -9999.0, shared)
        east_heightmap = normalize_heightmap(east, -9999.0, shared)

        self.assertEqual(shared, ElevationRange(100.0, 150.0))
        np.testing.assert_array_equal(west_heightmap[:, 1], east_heightmap[:, 0])
        np.testing.assert_allclose(west_heightmap, [[0.0, 0.2], [0.4, 0.6]])

    def test_maps_nodata_to_zero_without_affecting_the_range(self) -> None:
        elevation = np.array([[10.0, -9999.0], [15.0, 20.0]], dtype=np.float32)

        shared = calculate_elevation_range(((elevation, -9999.0),))
        heightmap = normalize_heightmap(elevation, -9999.0, shared)

        self.assertEqual(shared, ElevationRange(10.0, 20.0))
        self.assertEqual(heightmap[0, 1], 0.0)

    def test_rejects_an_all_nodata_import(self) -> None:
        elevation = np.full((2, 2), -9999.0, dtype=np.float32)

        with self.assertRaises(RasterFormatError):
            calculate_elevation_range(((elevation, -9999.0),))


if __name__ == "__main__":
    unittest.main()
