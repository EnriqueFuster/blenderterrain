from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from blender_terrain.core import (
    BBoxWGS84,
    DeliveryResult,
    DiscoveryResult,
    TransferProgress,
    create_import_plan,
    deliver_plan_sources,
)
from blender_terrain.errors import JobCancelled, ProviderUnavailableError
from blender_terrain.models import CatalogItem, DatasetProduct, ProjectedBounds


class FakeElevationClient:
    def download_item(
        self, item: CatalogItem, cache_directory: Path, maximum_bytes: int = 1_073_741_824,
        progress_callback=None,
    ) -> Path:
        cache_directory.mkdir(parents=True, exist_ok=True)
        destination = cache_directory / item.filename
        destination.write_bytes(b"downloaded tiff")
        if progress_callback:
            progress_callback(15, 15)
        return destination


class FakeImageryClient:
    def download_png(
        self, bounds: ProjectedBounds, width: int, height: int, cache_directory: Path,
        filename: str, progress_callback=None,
    ) -> Path:
        cache_directory.mkdir(parents=True, exist_ok=True)
        destination = cache_directory / filename
        destination.write_bytes(b"downloaded png")
        if progress_callback:
            progress_callback(14, None)
        return destination


class OfflineImageryClient:
    def download_png(self, *args, **kwargs) -> Path:
        raise ProviderUnavailableError("WMS unavailable")


class DeliveryTests(unittest.TestCase):
    def test_delivers_elevation_and_optional_pnoa_to_separate_cache_areas(self) -> None:
        plan = create_import_plan(
            BBoxWGS84(-0.39, 39.46, -0.37, 39.48),
            DatasetProduct.MDT02, 10.0, True, 5.0,
        )
        discovery = DiscoveryResult(
            (CatalogItem(DatasetProduct.MDT02, "source.tif", "COG", "1"),), 1, 0
        )
        events: list[TransferProgress] = []
        with TemporaryDirectory() as temporary:
            result = deliver_plan_sources(
                plan, discovery, Path(temporary), FakeElevationClient(),
                FakeImageryClient(), events.append,
            )

            self.assertIsInstance(result, DeliveryResult)
            self.assertEqual(len(result.elevation_paths), 1)
            self.assertEqual(len(result.imagery_paths), 1)
            self.assertEqual([event.kind for event in events], ["elevation", "imagery"])
            self.assertEqual(result.elevation_paths[0].parent.name, "elevation")
            self.assertEqual(result.imagery_paths[0].parent.parent.name, "imagery")

    def test_keeps_elevation_when_optional_pnoa_is_unavailable(self) -> None:
        plan = create_import_plan(
            BBoxWGS84(-0.39, 39.46, -0.37, 39.48),
            DatasetProduct.MDT02, 10.0, True, 5.0,
        )
        discovery = DiscoveryResult(
            (CatalogItem(DatasetProduct.MDT02, "source.tif", "COG", "1"),), 1, 0
        )
        with TemporaryDirectory() as temporary:
            result = deliver_plan_sources(
                plan, discovery, Path(temporary), FakeElevationClient(),
                OfflineImageryClient(),
            )

        self.assertEqual(len(result.elevation_paths), 1)
        self.assertEqual(result.imagery_paths, ())
        self.assertIn("WMS unavailable", result.warnings[0])

    def test_stops_before_delivery_when_cancelled(self) -> None:
        plan = create_import_plan(
            BBoxWGS84(-0.39, 39.46, -0.37, 39.48),
            DatasetProduct.MDT02, 10.0, False, None,
        )
        discovery = DiscoveryResult(
            (CatalogItem(DatasetProduct.MDT02, "source.tif", "COG", "1"),), 1, 0
        )
        with TemporaryDirectory() as temporary, self.assertRaises(JobCancelled):
            deliver_plan_sources(
                plan, discovery, Path(temporary), FakeElevationClient(),
                FakeImageryClient(), cancellation_requested=lambda: True,
            )

    def test_reports_validated_cache_reuse(self) -> None:
        plan = create_import_plan(
            BBoxWGS84(-0.39, 39.46, -0.37, 39.48),
            DatasetProduct.MDT02, 10.0, False, None,
        )
        discovery = DiscoveryResult(
            (CatalogItem(DatasetProduct.MDT02, "source.tif", "COG", "1"),), 1, 0
        )
        events: list[TransferProgress] = []
        with TemporaryDirectory() as temporary:
            elevation = Path(temporary) / "elevation" / "source.tif"
            elevation.parent.mkdir()
            elevation.write_bytes(
                b"II\x2b\x00\x08\x00\x00\x00\x10\x00\x00\x00\x00\x00\x00\x00"
                + b"\x00" * 8
            )
            result = deliver_plan_sources(
                plan, discovery, Path(temporary), FakeElevationClient(),
                FakeImageryClient(), events.append,
            )

        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].cached)
        self.assertEqual(events[0].written_bytes, events[0].expected_bytes)
        self.assertEqual(result.cached_elevation_count, 1)
        self.assertEqual(result.cached_imagery_count, 0)


if __name__ == "__main__":
    unittest.main()
