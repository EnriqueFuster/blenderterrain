"""Run the confirmed Copernicus GLO-30 pipeline outside Blender."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import numpy as np

from blender_terrain.catalog import (
    AcquisitionRequest,
    Catalog,
    DatasetKind,
    ImplementationStatus,
    LayerRequest,
    ProductSelection,
    SelectionBundle,
    SelectionMode,
    create_acquisition_plan,
    discover_candidates,
    load_bundled_catalog,
)
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.jobs import AcquisitionJob, run_confirmed_acquisition_job
from blender_terrain.jobs.storage import (
    read_progress_events,
    write_acquisition_job,
)

PRODUCT_ID = "COPERNICUS_GLO30_2021"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=Path, help="Directory used for downloaded data")
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        default=(-0.39, 39.46, -0.38, 39.47),
    )
    parser.add_argument("--resolution", type=float, default=100.0)
    arguments = parser.parse_args()
    roi = BBoxWGS84(*arguments.bbox)
    catalog = _development_catalog()
    product = catalog.product(PRODUCT_ID)
    request = AcquisitionRequest(
        roi,
        (LayerRequest(DatasetKind.DSM, arguments.resolution),),
    )
    selection = ProductSelection(
        product.provider_id,
        product.id,
        DatasetKind.DSM,
        SelectionMode.MANUAL,
        True,
    )
    plan = create_acquisition_plan(
        request,
        SelectionBundle((selection,)),
        (discover_candidates(catalog, roi, DatasetKind.DSM),),
    )
    task_id = str(uuid4())
    job_path = arguments.cache / "jobs" / task_id / "job.json"
    write_acquisition_job(
        job_path,
        AcquisitionJob(task_id, str(uuid4()), plan),
    )
    state = run_confirmed_acquisition_job(job_path)
    events, _offset = read_progress_events(job_path.with_name("events.jsonl"))
    for event in events:
        print(f"{event.progress * 100:5.1f}% {event.message}")
    if state.value != "COMPLETE":
        raise RuntimeError(f"Acquisition worker finished with {state.value}")
    result = json.loads(job_path.with_name("result.json").read_text(encoding="utf-8"))
    valid_samples = sum(
        int(np.count_nonzero(data != float(entry["nodata"])))
        for entry in result["processed_elevation"]
        for data in (np.load(entry["path"], allow_pickle=False),)
    )
    if valid_samples == 0:
        raise RuntimeError("GLO-30 processing produced no valid elevation samples")
    print(
        f"Prepared {len(result['processed_elevation'])} terrain tile(s), "
        f"{valid_samples} valid samples at "
        f"{result['request']['elevation_resolution_metres']:g} m"
    )
    return 0


def _development_catalog() -> Catalog:
    catalog = load_bundled_catalog()
    return Catalog(
        tuple(
            replace(product, implementation_status=ImplementationStatus.EXPERIMENTAL)
            if product.id == PRODUCT_ID
            else product
            for product in catalog.products
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
