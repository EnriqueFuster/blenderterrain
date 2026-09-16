"""Probe NH24 source windows and one small end-to-end acquisition."""

import argparse
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from blender_terrain.catalog import (
    DatasetKind,
    LayerRequest,
    ProductSelection,
    SelectionMode,
    load_bundled_catalog,
)
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.errors import NoCoverageError
from blender_terrain.io.elevation_window import ElevationWindowReader
from blender_terrain.providers.british_grid import BritishGridTransform, bundled_ostn15_path
from blender_terrain.providers.scottish_lidar import (
    ScottishLidarAcquirer,
    open_scottish_lidar_reader,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acquire", action="store_true", help="Extract a small valid NH24 ROI")
    parser.add_argument("--survey", action="store_true", help="Sample a bounded 8 x 8 NoData grid")
    args = parser.parse_args()
    catalog = load_bundled_catalog()
    cache = Path(".artifacts/scottish-nh24-probe")
    center_windows: dict[str, NDArray[np.float32]] = {}
    dtm_reference = None
    for kind in ("DTM", "DSM"):
        product = catalog.product(f"GB_SCT_SRSP_PHASE1_NH24_{kind}")
        reader = open_scottish_lidar_reader(product, cache / kind.lower())
        if kind == "DTM":
            dtm_reference = reader.georeference
        for label, row, column in (
            ("center", 1900, 1900),
            ("northwest", 0, 0),
            ("southeast", 3900, 3900),
        ):
            data = reader.read_window(row, column, 100, 100)
            if label == "center":
                center_windows[kind] = data
            valid = data[data != reader.nodata]
            print(
                product.id,
                label,
                f"valid={valid.size}/{data.size}",
                f"range={float(valid.min()):.2f}..{float(valid.max()):.2f}"
                if valid.size
                else "NoData",
            )
        if args.survey:
            print(kind, "sampled coverage (# full, + partial, . empty; not a footprint)")
            for survey_row in range(8):
                markers = []
                for survey_column in range(8):
                    row = round((survey_row + 0.5) * reader.layout.height / 8) - 16
                    column = round((survey_column + 0.5) * reader.layout.width / 8) - 16
                    sample = reader.read_window(row, column, 32, 32)
                    count = np.count_nonzero(sample != reader.nodata)
                    markers.append("#" if count == sample.size else "+" if count else ".")
                print("".join(markers))
    difference = center_windows["DSM"] - center_windows["DTM"]
    print(
        "DSM minus DTM center",
        f"changed={np.count_nonzero(difference)}/{difference.size}",
        f"range={float(difference.min()):.2f}..{float(difference.max()):.2f}",
    )
    if args.acquire:
        assert dtm_reference is not None
        easting = dtm_reference.origin_x + 1950 * dtm_reference.pixel_width
        northing = dtm_reference.origin_y + 1950 * dtm_reference.pixel_height
        longitude, latitude = BritishGridTransform(bundled_ostn15_path()).inverse(easting, northing)
        roi = BBoxWGS84(
            longitude - 0.0005,
            latitude - 0.0003,
            longitude + 0.0005,
            latitude + 0.0003,
        )
        print("Acquisition ROI", roi)
        acquirer = ScottishLidarAcquirer(catalog)
        for kind in (DatasetKind.DTM, DatasetKind.DSM):
            product = catalog.product(f"GB_SCT_SRSP_PHASE1_{kind.name}")
            selection = ProductSelection(
                product.provider_id, product.id, kind, SelectionMode.MANUAL, True
            )
            try:
                acquired = acquirer.acquire(selection, LayerRequest(kind), roi, cache / "acquired")
            except NoCoverageError:
                print(kind.name, "coverage=NONE")
                continue
            valid_cells = 0
            total_cells = 0
            for path in acquired.paths:
                window = ElevationWindowReader(path)
                data = np.load(path, mmap_mode="r", allow_pickle=False)
                valid = data[data != window.nodata]
                valid_cells += valid.size
                total_cells += data.size
                print(
                    kind.name,
                    path.name,
                    f"EPSG:{window.georeference.epsg}",
                    f"valid={valid.size}/{data.size}",
                    f"range={float(valid.min()):.2f}..{float(valid.max()):.2f}"
                    if valid.size
                    else "NoData",
                )
            print(kind.name, f"cached={acquired.cached_count}/{len(acquired.paths)}")
            print(
                kind.name,
                "coverage=FULL" if valid_cells == total_cells else "coverage=PARTIAL",
                f"valid={valid_cells}/{total_cells}",
            )


if __name__ == "__main__":
    main()
