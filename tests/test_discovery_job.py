from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from blender_terrain.core import BBoxWGS84
from blender_terrain.errors import CatalogContractChanged, ProviderUnavailableError
from blender_terrain.jobs.models import DiscoveryJob, JobState
from blender_terrain.jobs.storage import (
    is_cancellation_requested,
    read_discovery_job,
    request_cancellation,
    write_discovery_job,
)
from blender_terrain.jobs.worker import run_discovery_job
from blender_terrain.models import CatalogItem, CatalogPage, DatasetProduct


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


def job() -> DiscoveryJob:
    return DiscoveryJob(
        job_id=str(uuid4()),
        bounds=BBoxWGS84(-0.39, 39.46, -0.37, 39.48),
        product=DatasetProduct.MDT02,
        elevation_resolution_metres=10.0,
        use_imagery=False,
        imagery_gsd_metres=None,
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


if __name__ == "__main__":
    unittest.main()
