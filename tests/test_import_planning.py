from __future__ import annotations

import unittest

from blender_terrain.core import BBoxWGS84
from blender_terrain.core import create_import_plan as _create_import_plan
from blender_terrain.errors import NoCoverageError, PlanningLimitExceeded, UserInputError
from blender_terrain.models import DatasetProduct, ProjectedBounds


def create_import_plan(*args: object, **kwargs: object):
    """Build plans for the 2 m CNIG fixture used by most tests in this module."""

    kwargs.setdefault("native_resolution_override", 2.0)
    return _create_import_plan(*args, **kwargs)  # type: ignore[arg-type]


class ImportPlanningTests(unittest.TestCase):
    def test_auto_respects_each_products_native_resolution(self) -> None:
        bounds = BBoxWGS84(-0.39, 39.46, -0.37, 39.48)

        self.assertEqual(
            create_import_plan(
                bounds,
                DatasetProduct.MDT50CM,
                None,
                False,
                None,
                native_resolution_override=0.5,
            ).elevation_resolution_metres,
            0.5,
        )
        self.assertEqual(
            create_import_plan(
                bounds,
                DatasetProduct.MDT25,
                None,
                False,
                None,
                native_resolution_override=25.0,
            ).elevation_resolution_metres,
            25.0,
        )
        self.assertEqual(
            create_import_plan(
                bounds,
                DatasetProduct.MDT200,
                None,
                False,
                None,
                native_resolution_override=200.0,
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

    def test_preserves_valencia_spanish_planning_baseline(self) -> None:
        plan = create_import_plan(
            BBoxWGS84(-0.39, 39.46, -0.37, 39.48),
            DatasetProduct.MDT02,
            10.0,
            True,
            5.0,
        )

        self.assertEqual(
            plan.grids[0].bounds,
            ProjectedBounds(724_480, 4_371_070, 726_270, 4_373_350, 25830),
        )
        self.assertEqual((plan.grids[0].columns, plan.grids[0].rows), (179, 228))
        self.assertEqual(plan.elevation_sample_count, 40_812)
        self.assertEqual(plan.terrain_tile_count, 1)
        assert plan.imagery is not None
        self.assertEqual(
            (plan.imagery.pixel_width, plan.imagery.pixel_height),
            (344, 445),
        )

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

    def test_requires_native_resolution_from_the_data_source(self) -> None:
        with self.assertRaisesRegex(UserInputError, "Native elevation resolution"):
            _create_import_plan(
                BBoxWGS84(-0.39, 39.46, -0.37, 39.48),
                "ANY_ELEVATION_PRODUCT",
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

    def test_global_imagery_auto_coarsens_from_native_resolution(self) -> None:
        plan = create_import_plan(
            BBoxWGS84(1.0, 54.0, 3.0, 56.0),
            "COPERNICUS_GLO30_2021",
            100.0,
            True,
            None,
            maximum_imagery_pixels=16_777_216,
            native_resolution_override=30.0,
            use_global_utm=True,
            imagery_native_resolution_metres=10.0,
        )

        self.assertIsNotNone(plan.imagery)
        assert plan.imagery is not None
        self.assertGreaterEqual(plan.imagery.gsd_metres, 50.0)

    def test_builds_french_catalog_plan_in_lambert93(self) -> None:
        plan = create_import_plan(
            BBoxWGS84(2.34, 48.85, 2.36, 48.87),
            "FR_RGE_ALTI_1M",
            2.0,
            False,
            None,
            native_resolution_override=1.0,
            working_crs_epsg=2154,
        )

        self.assertEqual([area.crs.epsg for area in plan.work_areas], [2154])
        self.assertEqual([grid.bounds.epsg for grid in plan.grids], [2154])
        self.assertFalse(plan.crosses_utm_zones)

    def test_rejects_lambert93_outside_metropolitan_envelope(self) -> None:
        with self.assertRaisesRegex(NoCoverageError, "metropolitan France"):
            create_import_plan(
                BBoxWGS84(-61.6, 16.1, -61.5, 16.2),
                "FR_RGE_ALTI_1M",
                10.0,
                False,
                None,
                native_resolution_override=1.0,
                working_crs_epsg=2154,
            )

    def test_imagery_cannot_be_requested_finer_than_source(self) -> None:
        with self.assertRaisesRegex(UserInputError, "finer than the source"):
            create_import_plan(
                BBoxWGS84(1.0, 54.0, 1.1, 54.1),
                "COPERNICUS_GLO30_2021",
                30.0,
                True,
                5.0,
                native_resolution_override=30.0,
                use_global_utm=True,
                imagery_native_resolution_metres=10.0,
            )


if __name__ == "__main__":
    unittest.main()
