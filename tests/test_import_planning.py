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
        self.assertLessEqual(plan.elevation.sample_count, 16_777_216)

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


if __name__ == "__main__":
    unittest.main()
