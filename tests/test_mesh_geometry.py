from __future__ import annotations

import unittest

import numpy as np

from blender_terrain.core import (
    build_displacement_mesh_geometry,
    build_terrain_mesh_geometry,
)
from blender_terrain.models import ProjectedBounds


class TerrainMeshGeometryTests(unittest.TestCase):
    def test_builds_flat_displacement_grid_with_the_baked_topology(self) -> None:
        elevation = np.array([[100, 110], [120, 130]], dtype=np.float32)
        bounds = ProjectedBounds(500000, 4300000, 500010, 4300010, 25830)

        baked = build_terrain_mesh_geometry(elevation, bounds, -9999.0)
        displaced = build_displacement_mesh_geometry(
            elevation, bounds, -9999.0, baseline=100.0
        )

        np.testing.assert_array_equal(displaced.vertices[:, :2], baked.vertices[:, :2])
        np.testing.assert_array_equal(displaced.vertices[:, 2], 100.0)
        np.testing.assert_array_equal(displaced.faces, baked.faces)

    def test_builds_local_north_up_vertices_and_counter_clockwise_quads(self) -> None:
        elevation = np.array([[10, 11, 12], [20, 21, 22]], dtype=np.float32)

        geometry = build_terrain_mesh_geometry(
            elevation, ProjectedBounds(100, 200, 104, 202, 25830), -9999.0
        )

        np.testing.assert_array_equal(
            geometry.vertices,
            np.array(
                [
                    [0, 2, 10], [2, 2, 11], [4, 2, 12],
                    [0, 0, 20], [2, 0, 21], [4, 0, 22],
                ],
                dtype=np.float32,
            ),
        )
        np.testing.assert_array_equal(
            geometry.faces, np.array([[0, 3, 4, 1], [1, 4, 5, 2]], dtype=np.int32)
        )

    def test_omits_faces_touching_nodata(self) -> None:
        elevation = np.array([[10, -9999, 12], [20, 21, 22]], dtype=np.float32)

        geometry = build_terrain_mesh_geometry(
            elevation, ProjectedBounds(100, 200, 104, 202, 25830), -9999.0
        )

        self.assertEqual(geometry.faces.shape, (0, 4))
        self.assertEqual(geometry.vertices[1, 2], 0.0)


if __name__ == "__main__":
    unittest.main()
