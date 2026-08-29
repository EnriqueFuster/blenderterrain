from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from blender_terrain.catalog import (
    AcquisitionPlan,
    AcquisitionRequest,
    DatasetKind,
    LayerRequest,
    ProductSelection,
    SelectionBundle,
    SelectionMode,
)
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.jobs.acquisition_job import AcquisitionJob
from blender_terrain.jobs.models import JobState, ProgressEvent
from blender_terrain.jobs.storage import (
    append_progress_event,
    read_acquisition_job,
    read_progress_events,
    write_acquisition_job,
)


class ProgressStorageTests(unittest.TestCase):
    def test_round_trips_confirmed_acquisition_job(self) -> None:
        request = AcquisitionRequest(
            BBoxWGS84(-0.39, 39.46, -0.38, 39.47),
            (LayerRequest(DatasetKind.DSM, 100.0),),
        )
        selection = ProductSelection(
            "copernicus_dem",
            "COPERNICUS_GLO30_2021",
            DatasetKind.DSM,
            SelectionMode.MANUAL,
            True,
        )
        job = AcquisitionJob(
            str(uuid4()),
            str(uuid4()),
            AcquisitionPlan(request, SelectionBundle((selection,))),
        )
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "job.json"
            write_acquisition_job(path, job)

            restored = read_acquisition_job(path)

        self.assertEqual(restored, job)

    def test_reads_only_new_complete_events(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            first = ProgressEvent(0, JobState.DISCOVERING, 0.2, "Finding files")
            second = ProgressEvent(1, JobState.DOWNLOADING_ELEVATION, 0.5, "Downloading")
            append_progress_event(path, first)

            events, offset = read_progress_events(path)
            self.assertEqual(events, (first,))

            append_progress_event(path, second)
            events, next_offset = read_progress_events(path, offset)
            self.assertEqual(events, (second,))
            self.assertGreater(next_offset, offset)

    def test_leaves_an_incomplete_final_record_for_the_next_read(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            path.write_bytes(b'{"sequence":0')

            events, offset = read_progress_events(path)

            self.assertEqual(events, ())
            self.assertEqual(offset, 0)


if __name__ == "__main__":
    unittest.main()
