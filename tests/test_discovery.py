from __future__ import annotations

import unittest

from blender_terrain.core import (
    BBoxWGS84,
    create_import_plan,
)
from blender_terrain.errors import CatalogContractChanged, NoCoverageError
from blender_terrain.models import CatalogItem, CatalogPage, DatasetProduct
from blender_terrain.providers.cnig_discovery import discover_sources, select_catalog_items
from blender_terrain.providers.spain_crs import split_spain_bbox_by_utm_zone


def item(
    filename: str,
    sequential_id: str,
    product: DatasetProduct = DatasetProduct.MDT02,
) -> CatalogItem:
    return CatalogItem(product, filename, "COG", sequential_id, size_mb=10.0)


class DiscoverySelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = create_import_plan(
            BBoxWGS84(-0.39, 39.46, -0.37, 39.48),
            DatasetProduct.MDT02,
            10.0,
            False,
            None,
            native_resolution_override=2.0,
            work_areas_override=split_spain_bbox_by_utm_zone(
                BBoxWGS84(-0.39, 39.46, -0.37, 39.48)
            ),
        )

    def test_selects_only_matching_native_zone_and_product(self) -> None:
        catalog = CatalogPage(
            4,
            (
                item("MDT02-ETRS89-HU30-0722.TIF", "1"),
                item("MDT02-WGS84-0722.TIF", "2"),
                item("MDT02-ETRS89-HU31-0722.TIF", "3"),
                item("MDS02-ETRS89-H30-0722.TIF", "4", DatasetProduct.MDS02),
            ),
        )

        result = select_catalog_items(self.plan, catalog)

        self.assertEqual([selected.sequential_id for selected in result.items], ["1"])
        self.assertEqual(result.ignored_items, 3)
        self.assertEqual(result.estimated_download_mb, 10.0)

    def test_rejects_native_filename_without_zone(self) -> None:
        catalog = CatalogPage(1, (item("MDT02-ETRS89-UNKNOWN.TIF", "1"),))

        with self.assertRaises(CatalogContractChanged):
            select_catalog_items(self.plan, catalog)

    def test_reports_no_native_coverage(self) -> None:
        catalog = CatalogPage(1, (item("MDT02-WGS84-0722.TIF", "1"),))

        with self.assertRaises(NoCoverageError):
            select_catalog_items(self.plan, catalog)

    def test_reports_unknown_download_size(self) -> None:
        catalog = CatalogPage(
            1,
            (CatalogItem(DatasetProduct.MDT02, "MDT02-ETRS89-HU30-X.TIF", "COG", "1"),),
        )

        result = select_catalog_items(self.plan, catalog)

        self.assertIsNone(result.estimated_download_mb)

    def test_orchestrates_provider_discovery_from_import_plan(self) -> None:
        expected = CatalogPage(1, (item("MDT02-ETRS89-HU30-X.TIF", "1"),))

        class Provider:
            def discover_all(
                self, product: DatasetProduct, bbox: BBoxWGS84
            ) -> CatalogPage:
                self.arguments = (product, bbox)
                return expected

        provider = Provider()
        result = discover_sources(self.plan, provider)

        self.assertEqual(result.items, expected.items)
        self.assertEqual(provider.arguments, (self.plan.product, self.plan.bounds))

if __name__ == "__main__":
    unittest.main()
