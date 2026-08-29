"""Run the confirmed Copernicus GLO-30 pipeline outside Blender."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

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
from blender_terrain.core.delivery import TransferProgress
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.jobs import prepare_confirmed_elevation

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
    prepared = prepare_confirmed_elevation(
        plan,
        catalog,
        arguments.cache,
        transfer_callback=_report_transfer,
        processing_callback=lambda completed, total: print(
            f"Processing terrain: {completed}/{total}"
        ),
    )
    valid_samples = sum(
        int(np.count_nonzero(tile.data != tile.nodata)) for tile in prepared.tiles
    )
    if valid_samples == 0:
        raise RuntimeError("GLO-30 processing produced no valid elevation samples")
    print(
        f"Prepared {len(prepared.tiles)} terrain tile(s), "
        f"{valid_samples} valid samples at "
        f"{prepared.import_plan.elevation_resolution_metres:g} m"
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


def _report_transfer(transfer: TransferProgress) -> None:
    total = "?" if transfer.expected_bytes is None else str(transfer.expected_bytes)
    source = "cache" if transfer.cached else "network"
    print(
        f"{source}: {transfer.filename} "
        f"({transfer.written_bytes}/{total} bytes)"
    )


if __name__ == "__main__":
    raise SystemExit(main())
