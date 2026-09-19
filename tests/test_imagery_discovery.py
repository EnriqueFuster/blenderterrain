from __future__ import annotations

import json
from pathlib import Path
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
from blender_terrain.jobs.imagery_discovery import run_imagery_discovery_job
from blender_terrain.jobs.models import JobState
from blender_terrain.jobs.storage import write_acquisition_job
from blender_terrain.providers.sentinel2 import Sentinel2Scene


def test_discovers_confirmed_sentinel_scenes_without_downloading_bands(tmp_path: Path) -> None:
    roi = BBoxWGS84(-0.15, 51.49, -0.14, 51.50)
    policy = "2026-06-01T00:00:00Z/2026-09-01T23:59:59Z;cloud=20"
    request = AcquisitionRequest(roi, (LayerRequest(DatasetKind.IMAGERY, 10.0, policy),))
    selection = ProductSelection(
        "sentinel2",
        "SENTINEL2_L2A",
        DatasetKind.IMAGERY,
        SelectionMode.MANUAL,
        True,
        policy,
    )
    job_path = tmp_path / "job.json"
    write_acquisition_job(
        job_path,
        AcquisitionJob(
            str(uuid4()),
            str(uuid4()),
            AcquisitionPlan(request, SelectionBundle((selection,))),
        ),
    )
    scene = Sentinel2Scene(
        "S2A_T30UXC_20260729T111649_L2A",
        "2026-07-29T11:16:49Z",
        1.25,
        32630,
        BBoxWGS84(-1.0, 51.0, 0.0, 52.0),
        "https://e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com/scene/B04.tif",
        "https://e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com/scene/B03.tif",
        "https://e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com/scene/B02.tif",
        "https://e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com/scene/SCL.tif",
    )

    class Catalog:
        def search(self, bounds, start, end, cloud):
            assert bounds == roi
            assert (start, end, cloud) == (
                "2026-06-01T00:00:00Z",
                "2026-09-01T23:59:59Z",
                20.0,
            )
            return (scene,)

    state = run_imagery_discovery_job(job_path, lambda: Catalog())
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))

    assert state is JobState.COMPLETE
    assert result["sentinel2"] == {
        "scene_count": 1,
        "scene_ids": [scene.id],
        "earliest_acquisition": scene.acquired_at,
        "latest_acquisition": scene.acquired_at,
        "minimum_cloud_percent": 1.25,
        "maximum_cloud_percent": 1.25,
        "coverage_status": "complete",
    }
