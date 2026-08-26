from __future__ import annotations

import unittest

from blender_terrain.core import BBoxWGS84, create_import_plan
from blender_terrain.errors import PlanningLimitExceeded, UserInputError
from blender_terrain.models import DatasetProduct


class ImportPlanningTests(unittest.TestCase):
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

        self.assertEqual(plan.elevation_resolution_metres, 50.0)
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


if __name__ == "__main__":
    unittest.main()
