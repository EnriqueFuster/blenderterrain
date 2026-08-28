from __future__ import annotations

import unittest

from blender_terrain.core import BBoxWGS84, create_import_plan
from blender_terrain.errors import PlanningLimitExceeded, UserInputError
from blender_terrain.models import DatasetProduct


class ImportPlanningTests(unittest.TestCase):
    def test_auto_respects_each_products_native_resolution(self) -> None:
        bounds = BBoxWGS84(-0.39, 39.46, -0.37, 39.48)

        self.assertEqual(
            create_import_plan(
                bounds, DatasetProduct.MDT50CM, None, False, None
            ).elevation_resolution_metres,
            0.5,
        )
        self.assertEqual(
            create_import_plan(
                bounds, DatasetProduct.MDT25, None, False, None
            ).elevation_resolution_metres,
            25.0,
        )
        self.assertEqual(
            create_import_plan(
                bounds, DatasetProduct.MDT200, None, False, None
            ).elevation_resolution_metres,
            200.0,
        )

    def test_builds_an_mdt_plan_without_imagery(self) -> None:
        plan = create_import_plan(
            BBoxWGS84(-0.39, 39.46, -0.37, 39.48),
            DatasetProduct.MDT02,
            10.0,
            False,
            None,
        )

        self.assertEqual(plan.product, DatasetProduct.MDT02)
        self.assertEqual(plan.elevation_resolution_metres, 10.0)
        self.assertEqual(plan.terrain_tile_count, 1)
        self.assertGreater(plan.estimated_elevation_working_bytes, 0)
        self.assertEqual(plan.estimated_imagery_decoded_bytes, 0)
        self.assertIsNone(plan.imagery)

    def test_auto_selects_a_safe_elevation_resolution(self) -> None:
        plan = create_import_plan(
            BBoxWGS84(-4.0, 39.0, -3.0, 40.0),
            DatasetProduct.MDS02,
            None,
            False,
            None,
        )

        self.assertEqual(plan.elevation_resolution_metres, 25.0)
        self.assertLessEqual(plan.elevation_sample_count, 16_777_216)

    def test_estimates_optional_imagery_tiles(self) -> None:
        plan = create_import_plan(
            BBoxWGS84(-0.39, 39.46, -0.37, 39.48),
            DatasetProduct.MDS02,
            5.0,
            True,
            0.5,
        )

        assert plan.imagery is not None
        self.assertEqual(plan.imagery.gsd_metres, 0.5)
        self.assertEqual(plan.imagery.tile_count, 2)

    def test_rejects_an_unsafe_manual_resolution(self) -> None:
        with self.assertRaises(PlanningLimitExceeded):
            create_import_plan(
                BBoxWGS84(-4.0, 39.0, -3.0, 40.0),
                DatasetProduct.MDT02,
                2.0,
                False,
                None,
            )

    def test_rejects_non_elevation_product(self) -> None:
        with self.assertRaises(UserInputError):
            create_import_plan(
                BBoxWGS84(-0.39, 39.46, -0.37, 39.48),
                DatasetProduct.PNOA_MA,
                10.0,
                False,
                None,
            )

    def test_builds_separate_grids_for_a_cross_zone_roi(self) -> None:
        plan = create_import_plan(
            BBoxWGS84(-0.02, 39.0, 0.02, 39.04),
            DatasetProduct.MDT02,
            10.0,
            False,
            None,
        )

        self.assertEqual([grid.bounds.epsg for grid in plan.grids], [25830, 25831])
        self.assertTrue(plan.crosses_utm_zones)
        self.assertIn("crosses UTM zones", plan.warnings[0])

    def test_builds_an_exact_manual_grid(self) -> None:
        plan = create_import_plan(
            BBoxWGS84(-0.39, 39.46, -0.37, 39.48),
            DatasetProduct.MDT02,
            10.0,
            False,
            None,
            manual_tile_rows=2,
            manual_tile_columns=3,
        )

        self.assertEqual(plan.terrain_tile_count, 6)
        self.assertEqual(plan.terrain_tile_rows, 2)
        self.assertEqual(plan.terrain_tile_columns, 3)
        self.assertEqual(len(plan.tiles_for_grid(0)), 6)

    def test_auto_resolution_accounts_for_manual_object_size(self) -> None:
        plan = create_import_plan(
            BBoxWGS84(-0.39, 39.46, -0.37, 39.48),
            DatasetProduct.MDT02,
            None,
            False,
            None,
            manual_tile_rows=1,
            manual_tile_columns=1,
        )

        tile = plan.tiles_for_grid(0)[0]
        self.assertLessEqual(tile.rows, 512)
        self.assertLessEqual(tile.columns, 512)

    def test_rejects_an_unsafe_requested_manual_layout(self) -> None:
        with self.assertRaises(PlanningLimitExceeded):
            create_import_plan(
                BBoxWGS84(-0.39, 39.46, -0.37, 39.48),
                DatasetProduct.MDT02,
                2.0,
                False,
                None,
                manual_tile_rows=1,
                manual_tile_columns=1,
            )

    def test_resource_profiles_change_auto_resolution(self) -> None:
        bounds = BBoxWGS84(-0.6, 39.2, -0.1, 39.7)

        conservative = create_import_plan(
            bounds, DatasetProduct.MDT02, None, False, None,
            maximum_elevation_samples=4_194_304,
        )
        balanced = create_import_plan(
            bounds, DatasetProduct.MDT02, None, False, None,
            maximum_elevation_samples=16_777_216,
        )

        self.assertGreaterEqual(
            conservative.elevation_resolution_metres,
            balanced.elevation_resolution_metres,
        )
        self.assertLessEqual(conservative.elevation_sample_count, 4_194_304)

    def test_large_profile_accepts_more_imagery_pixels(self) -> None:
        bounds = BBoxWGS84(-0.39, 39.46, -0.34, 39.51)

        with self.assertRaises(PlanningLimitExceeded):
            create_import_plan(
                bounds, DatasetProduct.MDT02, 20.0, True, 0.5,
                maximum_imagery_pixels=16_777_216,
            )
        plan = create_import_plan(
            bounds, DatasetProduct.MDT02, 20.0, True, 0.5,
            maximum_imagery_pixels=268_435_456,
        )

        self.assertIsNotNone(plan.imagery)


if __name__ == "__main__":
    unittest.main()
