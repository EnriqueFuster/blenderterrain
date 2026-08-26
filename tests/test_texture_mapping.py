from __future__ import annotations

import unittest

from blender_terrain.core import (
    TextureTransform,
    bounds_fully_covered,
    projected_texture_transform,
)
from blender_terrain.models import ProjectedBounds


class ProjectedTextureMappingTests(unittest.TestCase):
    def test_maps_normalized_terrain_coordinates_into_imagery_uv(self) -> None:
        terrain = ProjectedBounds(100, 200, 300, 400, 25830)
        imagery = ProjectedBounds(0, 100, 400, 500, 25830)

        transform = projected_texture_transform(terrain, imagery)

        self.assertEqual(transform, TextureTransform(0.5, 0.5, 0.25, 0.25))

    def test_rejects_non_intersecting_or_different_crs_imagery(self) -> None:
        terrain = ProjectedBounds(100, 200, 300, 400, 25830)

        self.assertIsNone(
            projected_texture_transform(
                terrain, ProjectedBounds(300, 200, 500, 400, 25830)
            )
        )

    def test_detects_complete_rectangular_union_and_internal_gap(self) -> None:
        terrain = ProjectedBounds(0, 0, 10, 10, 25830)
        complete = (
            ProjectedBounds(0, 0, 5, 10, 25830),
            ProjectedBounds(5, 0, 10, 10, 25830),
        )
        gap = (
            ProjectedBounds(0, 0, 4, 10, 25830),
            ProjectedBounds(6, 0, 10, 10, 25830),
        )

        self.assertTrue(bounds_fully_covered(terrain, complete))
        self.assertFalse(bounds_fully_covered(terrain, gap))
        self.assertIsNone(
            projected_texture_transform(
                terrain, ProjectedBounds(100, 200, 300, 400, 25831)
            )
        )


if __name__ == "__main__":
    unittest.main()
