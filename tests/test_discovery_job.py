from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import numpy as np

from blender_terrain.core import BBoxWGS84, ProcessedElevationTile
from blender_terrain.core.grid import tile_grid
from blender_terrain.errors import CatalogContractChanged, ProviderUnavailableError
from blender_terrain.jobs.models import DiscoveryJob, JobState
from blender_terrain.jobs.storage import (
    is_cancellation_requested,
    read_discovery_job,
    request_cancellation,
    write_discovery_job,
)
from blender_terrain.jobs.worker import run_delivery_job, run_discovery_job
from blender_terrain.models import CatalogItem, CatalogPage, DatasetProduct, ProjectedBounds


class FakeProvider:
    def discover_all(self, product: DatasetProduct, bbox: BBoxWGS84) -> CatalogPage:
        return CatalogPage(
            2,
            (
                CatalogItem(
                    product,
                    "MDT02-ETRS89-HU30-0722.TIF",
                    "COG",
                    "100",
                    size_mb=25.0,
                ),
                CatalogItem(product, "MDT02-WGS84-0722.TIF", "COG", "101", size_mb=30.0),
            ),
        )


class NoCoverageProvider:
    def discover_all(self, product: DatasetProduct, bbox: BBoxWGS84) -> CatalogPage:
        return CatalogPage(0, ())


class ChangedProvider:
    def discover_all(self, product: DatasetProduct, bbox: BBoxWGS84) -> CatalogPage:
        raise CatalogContractChanged("fixture contract changed")


class OfflineProvider:
    def discover_all(self, product: DatasetProduct, bbox: BBoxWGS84) -> CatalogPage:
        raise ProviderUnavailableError("provider unavailable")


class FakeDeliveryCNIG(FakeProvider):
    def download_item(
        self, item: CatalogItem, cache_directory: Path,
        maximum_bytes: int = 1_073_741_824, progress_callback=None,
    ) -> Path:
        cache_directory.mkdir(parents=True, exist_ok=True)
        destination = cache_directory / item.filename
        destination.write_bytes(b"fake tiff")
        if progress_callback:
            progress_callback(9, 9)
        return destination


class FakeDeliveryImagery:
    def download_png(
        self, bounds: ProjectedBounds, width: int, height: int, cache_directory: Path,
        filename: str, progress_callback=None,
    ) -> Path:
        cache_directory.mkdir(parents=True, exist_ok=True)
        destination = cache_directory / filename
        destination.write_bytes(b"fake png")
        if progress_callback:
            progress_callback(8, 8)
        return destination


def job(use_imagery: bool = False) -> DiscoveryJob:
    return DiscoveryJob(
        task_id=str(uuid4()),
        import_id=str(uuid4()),
        bounds=BBoxWGS84(-0.39, 39.46, -0.37, 39.48),
        product=DatasetProduct.MDT02,
        elevation_resolution_metres=10.0,
        use_imagery=use_imagery,
        imagery_gsd_metres=5.0 if use_imagery else None,
    )


class DiscoveryJobStorageTests(unittest.TestCase):
    def test_round_trips_versioned_job_json(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "job.json"
            expected = job()

            write_discovery_job(path, expected)

            self.assertEqual(read_discovery_job(path), expected)
            self.assertFalse(path.with_name("job.json.part").exists())

    def test_cancellation_request_is_idempotent(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)

            request_cancellation(directory)
            request_cancellation(directory)

            self.assertTrue(is_cancellation_requested(directory))


class DiscoveryWorkerTests(unittest.TestCase):
    def test_honours_cancellation_before_contacting_provider(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            job_path = directory / "job.json"
            write_discovery_job(job_path, job())
            request_cancellation(directory)

            state = run_discovery_job(job_path, provider_factory=FakeProvider)

            result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(state, JobState.CANCELLED)
            self.assertEqual(result["state"], "CANCELLED")
    def test_writes_progress_and_atomic_terminal_result(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            job_path = directory / "job.json"
            write_discovery_job(job_path, job())

            state = run_discovery_job(job_path, provider_factory=FakeProvider)

            self.assertEqual(state, JobState.COMPLETE)
            events = [
                json.loads(line)
                for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [event["state"] for event in events],
                ["VALIDATING", "DISCOVERING", "COMPLETE"],
            )
            self.assertEqual([event["sequence"] for event in events], [0, 1, 2])
            result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["state"], "COMPLETE")
            self.assertIn("task_id", result)
            self.assertIn("import_id", result)
            self.assertEqual(result["items"][0]["sequential_id"], "100")
            self.assertEqual(result["estimated_download_mb"], 25.0)
            self.assertFalse((directory / "result.json.part").exists())

    def test_preserves_distinct_terminal_error_states(self) -> None:
        examples = (
            (NoCoverageProvider, JobState.NO_COVERAGE),
            (ChangedProvider, JobState.PROVIDER_CHANGED),
            (OfflineProvider, JobState.NETWORK_ERROR),
        )
        for provider_factory, expected_state in examples:
            with self.subTest(expected_state=expected_state), TemporaryDirectory() as temporary:
                directory = Path(temporary)
                job_path = directory / "job.json"
                write_discovery_job(job_path, job())

                state = run_discovery_job(job_path, provider_factory=provider_factory)

                result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
                self.assertEqual(state, expected_state)
                self.assertEqual(result["state"], expected_state.value)
                self.assertIn("error", result)


class DeliveryWorkerTests(unittest.TestCase):
    def test_downloads_elevation_and_pnoa_and_persists_paths(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            job_path = directory / "jobs" / str(uuid4()) / "job.json"
            write_discovery_job(job_path, job(use_imagery=True))

            state = run_delivery_job(
                job_path,
                cnig_factory=FakeDeliveryCNIG,
                imagery_factory=FakeDeliveryImagery,
                elevation_processor=_fake_elevation_processor,
            )

            result = json.loads(job_path.with_name("result.json").read_text(encoding="utf-8"))
            events = [
                json.loads(line)
                for line in job_path.with_name("events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(state, JobState.COMPLETE)
            self.assertEqual(len(result["elevation_paths"]), 1)
            self.assertEqual(len(result["imagery_paths"]), 1)
            self.assertEqual(len(result["imagery"]), 1)
            self.assertEqual(result["imagery"][0]["bounds"]["epsg"], 25830)
            self.assertEqual(len(result["processed_elevation"]), 1)
            self.assertTrue(Path(result["processed_elevation"][0]["path"]).is_file())
            self.assertIn("DOWNLOADING_ELEVATION", [event["state"] for event in events])
            self.assertIn("DOWNLOADING_IMAGERY", [event["state"] for event in events])

    def test_honours_cancellation_before_network_delivery(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            job_path = Path(temporary_directory) / "jobs" / str(uuid4()) / "job.json"
            write_discovery_job(job_path, job())
            request_cancellation(job_path.parent)

            state = run_delivery_job(job_path, cnig_factory=FakeDeliveryCNIG)

            self.assertEqual(state, JobState.CANCELLED)


def _fake_elevation_processor(
    paths: tuple[Path, ...], plan,
) -> tuple[ProcessedElevationTile, ...]:
    tile = tile_grid(plan.grids[0])[0]
    data = np.zeros((tile.rows + 1, tile.columns + 1), dtype=np.float32)
    return (ProcessedElevationTile(0, tile, data, -9999.0, 0, 0, 0.0),)


if __name__ == "__main__":
    unittest.main()
