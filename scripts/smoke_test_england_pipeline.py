"""Run a small Environment Agency elevation pipeline outside Blender."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

import numpy as np

from blender_terrain.catalog import (
    AcquisitionRequest,
    LayerRequest,
    ProductSelection,
    SelectionBundle,
    SelectionMode,
    create_acquisition_plan,
    discover_candidates,
    load_bundled_catalog,
)
from blender_terrain.core import BBoxWGS84
from blender_terrain.jobs import AcquisitionJob, run_confirmed_acquisition_job
from blender_terrain.jobs.storage import read_progress_events, write_acquisition_job

PRODUCTS = {
    "dtm": "GB_ENG_EA_LIDAR_COMPOSITE_1M_DTM",
    "dsm": "GB_ENG_EA_LIDAR_COMPOSITE_1M_DSM_LAST_RETURN",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=Path)
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        default=(-0.13, 51.5, -0.129, 51.5005),
    )
    parser.add_argument("--product", choices=PRODUCTS, default="dtm")
    parser.add_argument("--resolution", type=float, default=5.0)
    arguments = parser.parse_args()

    roi = BBoxWGS84(*arguments.bbox)
    catalog = load_bundled_catalog()
    product = catalog.product(PRODUCTS[arguments.product])
    kind = product.capabilities.kind
    request = AcquisitionRequest(roi, (LayerRequest(kind, arguments.resolution),))
    selection = ProductSelection(
        product.provider_id, product.id, kind, SelectionMode.MANUAL, True
    )
    plan = create_acquisition_plan(
        request,
        SelectionBundle((selection,)),
        (discover_candidates(catalog, roi, kind),),
    )
    task_id = str(uuid4())
    job_path = arguments.cache.resolve() / "jobs" / task_id / "job.json"
    write_acquisition_job(job_path, AcquisitionJob(task_id, str(uuid4()), plan))
    state = run_confirmed_acquisition_job(job_path)
    events, _ = read_progress_events(job_path.with_name("events.jsonl"))
    for event in events:
        print(f"{event.progress * 100:5.1f}% {event.message}")
    if state.value != "COMPLETE":
        raise RuntimeError(f"English acquisition finished with {state.value}")

    result = json.loads(job_path.with_name("result.json").read_text(encoding="utf-8"))
    valid_samples = sum(
        int(np.count_nonzero(values != float(tile["nodata"])))
        for tile in result["processed_elevation"]
        for values in (np.load(tile["path"], allow_pickle=False),)
    )
    if valid_samples == 0 or {source["product_id"] for source in result["sources"]} != {
        product.id
    }:
        raise RuntimeError("English pipeline output is incomplete")
    print(f"Prepared {len(result['processed_elevation'])} tile(s), {valid_samples} valid samples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
