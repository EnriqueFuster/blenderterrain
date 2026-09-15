"""Probe small NH24 DTM/DSM windows without downloading whole assets."""

from pathlib import Path

import numpy as np

from blender_terrain.catalog import load_bundled_catalog
from blender_terrain.providers.scottish_lidar import open_scottish_lidar_reader


def main() -> None:
    catalog = load_bundled_catalog()
    cache = Path(".artifacts/scottish-nh24-probe")
    center_windows: dict[str, np.ndarray] = {}
    for kind in ("DTM", "DSM"):
        product = catalog.product(f"GB_SCT_SRSP_PHASE1_NH24_{kind}")
        reader = open_scottish_lidar_reader(product, cache / kind.lower())
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
    difference = center_windows["DSM"] - center_windows["DTM"]
    print(
        "DSM minus DTM center",
        f"changed={np.count_nonzero(difference)}/{difference.size}",
        f"range={float(difference.min()):.2f}..{float(difference.max()):.2f}",
    )


if __name__ == "__main__":
    main()
