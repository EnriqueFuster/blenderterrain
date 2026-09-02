"""Run a small French elevation and orthophoto pipeline outside Blender."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

import numpy as np

from blender_terrain.catalog import (
    AcquisitionRequest,
    DatasetKind,
    LayerRequest,
    ProductRecord,
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

ELEVATION_PRODUCTS = ("FR_RGE_ALTI_1M", "FR_MNS_CORREL_50CM")
IMAGERY_PRODUCT = "FR_BD_ORTHO"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=Path)
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        default=(2.34, 48.85, 2.342, 48.852),
    )
    parser.add_argument("--elevation-resolution", type=float, default=5.0)
    parser.add_argument("--imagery-resolution", type=float, default=2.0)
    parser.add_argument(
        "--elevation-product",
        choices=ELEVATION_PRODUCTS,
        default=ELEVATION_PRODUCTS[0],
    )
    arguments = parser.parse_args()

    roi = BBoxWGS84(*arguments.bbox)
    catalog = load_bundled_catalog()
    elevation_product = catalog.product(arguments.elevation_product)
    elevation_kind = elevation_product.capabilities.kind
    layers = (
        LayerRequest(elevation_kind, arguments.elevation_resolution),
        LayerRequest(DatasetKind.IMAGERY, arguments.imagery_resolution),
    )
    selections = SelectionBundle(
        (
            _selection(elevation_product, elevation_kind),
            _selection(catalog.product(IMAGERY_PRODUCT), DatasetKind.IMAGERY),
        )
    )
    plan = create_acquisition_plan(
        AcquisitionRequest(roi, layers),
        selections,
        tuple(
            discover_candidates(catalog, roi, kind)
            for kind in (elevation_kind, DatasetKind.IMAGERY)
        ),
    )
    task_id = str(uuid4())
    job_path = arguments.cache.resolve() / "jobs" / task_id / "job.json"
    write_acquisition_job(job_path, AcquisitionJob(task_id, str(uuid4()), plan))
    state = run_confirmed_acquisition_job(job_path)
    events, _ = read_progress_events(job_path.with_name("events.jsonl"))
    for event in events:
        print(f"{event.progress * 100:5.1f}% {event.message}")
    if state.value != "COMPLETE":
        raise RuntimeError(f"French acquisition finished with {state.value}")

    result = json.loads(job_path.with_name("result.json").read_text(encoding="utf-8"))
    valid_samples = sum(
        int(np.count_nonzero(values != float(tile["nodata"])))
        for tile in result["processed_elevation"]
        for values in (np.load(tile["path"], allow_pickle=False),)
    )
    source_products = {source["product_id"] for source in result["sources"]}
    if valid_samples == 0 or source_products != {
        arguments.elevation_product,
        IMAGERY_PRODUCT,
    }:
        raise RuntimeError("French pipeline output is incomplete")
    if not result["imagery"] or {crs["epsg"] for crs in result["crs"]} != {2154}:
        raise RuntimeError("French imagery or Lambert-93 metadata is missing")
    print(
        f"Prepared {len(result['processed_elevation'])} terrain tile(s), "
        f"{len(result['imagery'])} texture tile(s), and {valid_samples} valid samples"
    )
    return 0


def _selection(product: ProductRecord, kind: DatasetKind) -> ProductSelection:
    return ProductSelection(
        product.provider_id,
        product.id,
        kind,
        SelectionMode.MANUAL,
        True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
