from __future__ import annotations

import unittest

from blender_terrain.core import (
    BBoxWGS84,
    create_import_plan,
    plan_imagery_tiles,
    plan_texture_tiles,
)
from blender_terrain.models import DatasetProduct


class ImageryTilePlanningTests(unittest.TestCase):
    def test_creates_bounded_projected_tiles_for_pnoa(self) -> None:
        plan = create_import_plan(
            BBoxWGS84(-0.39, 39.46, -0.37, 39.48),
            DatasetProduct.MDT02,
            10.0,
            True,
            0.5,
        )

        requests = plan_imagery_tiles(plan)

        self.assertEqual(len(requests), 2)
        self.assertTrue(all(request.bounds.epsg == 25830 for request in requests))
        self.assertTrue(all(request.width <= 4096 for request in requests))
        self.assertTrue(all(request.height <= 4096 for request in requests))
        self.assertEqual(
            [request.filename for request in requests],
            [
                "pnoa_epsg25830_z0_r0_c0_0p5m.png",
                "pnoa_epsg25830_z0_r1_c0_0p5m.png",
            ],
        )

    def test_creates_separate_requests_when_roi_crosses_utm_zones(self) -> None:
        plan = create_import_plan(
            BBoxWGS84(-0.02, 39.0, 0.02, 39.04),
            DatasetProduct.MDT02,
            10.0,
            True,
            2.0,
        )

        requests = plan_imagery_tiles(plan)

        self.assertEqual({request.bounds.epsg for request in requests}, {25830, 25831})
        self.assertEqual({request.zone_index for request in requests}, {0, 1})

    def test_uses_provider_prefix_without_changing_the_texture_grid(self) -> None:
        plan = create_import_plan(
            BBoxWGS84(-0.39, 39.46, -0.37, 39.48),
            DatasetProduct.MDT02,
            10.0,
            True,
            2.0,
        )

        generic = plan_texture_tiles(plan, "worldcover")
        legacy = plan_imagery_tiles(plan)

        self.assertEqual([tile.bounds for tile in generic], [tile.bounds for tile in legacy])
        self.assertTrue(all(tile.filename.startswith("worldcover_") for tile in generic))

    def test_returns_no_requests_when_imagery_is_disabled(self) -> None:
        plan = create_import_plan(
            BBoxWGS84(-0.39, 39.46, -0.37, 39.48),
            DatasetProduct.MDT02,
            10.0,
            False,
            None,
        )

        self.assertEqual(plan_imagery_tiles(plan), ())

    def test_exact_tiles_respect_the_selected_large_profile(self) -> None:
        large_limit = 268_435_456
        plan = create_import_plan(
            BBoxWGS84(0.677894, 42.490376, 0.764869, 42.557514),
            DatasetProduct.MDS02,
            2.0,
            True,
            0.5,
            maximum_elevation_samples=67_108_864,
            maximum_imagery_pixels=large_limit,
        )

        requests = plan_imagery_tiles(plan)
        exact_pixels = sum(request.width * request.height for request in requests)

        self.assertGreater(exact_pixels, 67_108_864)
        self.assertLessEqual(exact_pixels, large_limit)


if __name__ == "__main__":
    unittest.main()
