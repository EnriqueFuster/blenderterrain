from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import numpy as np

from blender_terrain.core import BBoxWGS84, ProcessedElevationTile, create_import_plan
from blender_terrain.core.grid import tile_grid
from blender_terrain.errors import CatalogContractChanged, ProviderUnavailableError
from blender_terrain.jobs.cnig import (
    run_cnig_availability_job as run_availability_job,
)
from blender_terrain.jobs.cnig import run_cnig_discovery_job as run_discovery_job
from blender_terrain.jobs.local import run_local_delivery_job
from blender_terrain.jobs.models import DiscoveryJob, JobState
from blender_terrain.jobs.storage import (
    is_cancellation_requested,
    read_discovery_job,
    request_cancellation,
    write_discovery_job,
)
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


class PartialCoverageProvider(FakeProvider):
    def discover_all(self, product: DatasetProduct, bbox: BBoxWGS84) -> CatalogPage:
        if product is DatasetProduct.MDS50CM:
            return CatalogPage(0, ())
        return super().discover_all(product, bbox)


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

    def test_reads_previous_job_schema_without_local_imagery(self) -> None:
        expected = job()
        payload = expected.to_dict()
        payload["schema_version"] = 6
        payload.pop("local_imagery_path")
        payload.pop("local_imagery_bounds")
        payload.pop("local_imagery_width")
        payload.pop("local_imagery_height")

        self.assertEqual(DiscoveryJob.from_dict(payload), expected)

    def test_round_trips_manual_terrain_layout(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "job.json"
            base = job()
            expected = DiscoveryJob(
                task_id=base.task_id,
                import_id=base.import_id,
                bounds=base.bounds,
                product=base.product,
                elevation_resolution_metres=base.elevation_resolution_metres,
                use_imagery=base.use_imagery,
                imagery_gsd_metres=base.imagery_gsd_metres,
                manual_tile_rows=2,
                manual_tile_columns=3,
            )

            write_discovery_job(path, expected)

            self.assertEqual(read_discovery_job(path), expected)

    def test_round_trips_resource_limits(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "job.json"
            base = job()
            expected = DiscoveryJob(
                task_id=base.task_id,
                import_id=base.import_id,
                bounds=base.bounds,
                product=base.product,
                elevation_resolution_metres=base.elevation_resolution_metres,
                use_imagery=base.use_imagery,
                imagery_gsd_metres=base.imagery_gsd_metres,
                maximum_elevation_samples=67_108_864,
                maximum_imagery_pixels=268_435_456,
            )

            write_discovery_job(path, expected)

            self.assertEqual(read_discovery_job(path), expected)

    def test_round_trips_local_elevation_paths(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "job.json"
            base = job()
            expected = DiscoveryJob(
                task_id=base.task_id,
                import_id=base.import_id,
                bounds=base.bounds,
                product=base.product,
                elevation_resolution_metres=base.elevation_resolution_metres,
                use_imagery=False,
                imagery_gsd_metres=None,
                local_elevation_paths=("C:/data/tile-a.tif", "C:/data/tile-b.tif"),
            )

            write_discovery_job(path, expected)

            self.assertEqual(read_discovery_job(path), expected)

    def test_round_trips_local_imagery_metadata(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "job.json"
            base = job()
            expected = DiscoveryJob(
                task_id=base.task_id,
                import_id=base.import_id,
                bounds=base.bounds,
                product=base.product,
                elevation_resolution_metres=base.elevation_resolution_metres,
                use_imagery=True,
                imagery_gsd_metres=None,
                local_elevation_paths=("C:/data/elevation.tif",),
                local_imagery_path="C:/data/ortho.png",
                local_imagery_bounds=ProjectedBounds(
                    700_000, 4_370_000, 701_000, 4_371_000, 25830
                ),
                local_imagery_width=1_000,
                local_imagery_height=1_000,
            )

            write_discovery_job(path, expected)

            self.assertEqual(read_discovery_job(path), expected)

    def test_cancellation_request_is_idempotent(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)

            request_cancellation(directory)
            request_cancellation(directory)

            self.assertTrue(is_cancellation_requested(directory))


class DiscoveryWorkerTests(unittest.TestCase):
    def test_checks_availability_for_every_elevation_product(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            job_path = directory / "job.json"
            write_discovery_job(job_path, job())

            state = run_availability_job(
                job_path, provider_factory=PartialCoverageProvider
            )

            result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
            statuses = {
                entry["product"]: entry["status"] for entry in result["availability"]
            }
            self.assertEqual(state, JobState.COMPLETE)
            self.assertEqual(len(statuses), 8)
            self.assertEqual(statuses["MDS50CM"], "NO_COVERAGE")
            self.assertEqual(statuses["MDT02"], "AVAILABLE")

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
            self.assertEqual(result["schema_version"], 2)
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
    def test_processes_local_elevation_without_a_provider(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source = directory / "local-elevation.tif"
            source.write_bytes(b"local-test-raster")
            local_job = replace(job(), local_elevation_paths=(str(source),))
            job_path = directory / "jobs" / str(uuid4()) / "job.json"
            write_discovery_job(job_path, local_job)
            plan = create_import_plan(
                local_job.bounds,
                local_job.product,
                local_job.elevation_resolution_metres,
                False,
                None,
                native_resolution_override=2.0,
            )

            with (
                patch("blender_terrain.jobs.local.create_local_import_plan", return_value=plan),
                patch(
                    "blender_terrain.jobs.local.inspect_local_elevation",
                    return_value=SimpleNamespace(paths=(source.resolve(),)),
                ),
            ):
                state = run_local_delivery_job(
                    job_path,
                    elevation_processor=_fake_elevation_processor,
                )

            result = json.loads(job_path.with_name("result.json").read_text(encoding="utf-8"))
            self.assertEqual(state, JobState.COMPLETE)
            self.assertEqual(result["elevation_paths"], [str(source.resolve())])
            self.assertEqual(result["provenance"]["source"], "User-provided local elevation raster")
            self.assertTrue(Path(result["processed_elevation"][0]["path"]).is_file())

def _fake_elevation_processor(
    paths: tuple[Path, ...],
    plan,
    progress_callback=None,
    maximum_source_window_pixels=4_194_304,
    region=None,
) -> tuple[ProcessedElevationTile, ...]:
    tile = tile_grid(plan.grids[0])[0]
    data = np.zeros((tile.rows + 1, tile.columns + 1), dtype=np.float32)
    if progress_callback is not None:
        progress_callback(0, 1)
        progress_callback(1, 1)
    return (ProcessedElevationTile(0, tile, data, -9999.0, 0, 0, 0.0),)


if __name__ == "__main__":
    unittest.main()
