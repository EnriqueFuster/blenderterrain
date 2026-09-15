"""Inspect an official OSNI sample ZIP; do not infer its CRS from coordinates."""

import argparse
import io
from itertools import pairwise
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from blender_terrain.errors import RasterFormatError
from blender_terrain.providers.osni import iter_osni_xyz

MAX_SAMPLE_BYTES = 20_000_000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sample_zip", type=Path, help="Downloaded official 10m DTM sample ZIP")
    arguments = parser.parse_args()
    count = 0
    x_values: set[float] = set()
    y_values: set[float] = set()
    minimum_height = float("inf")
    maximum_height = float("-inf")
    try:
        with ZipFile(arguments.sample_zip) as archive:
            entries = [
                item for item in archive.infolist() if item.filename.lower().endswith(".txt")
            ]
            if len(entries) != 1 or entries[0].file_size > MAX_SAMPLE_BYTES:
                raise RasterFormatError("Expected one bounded TXT file in the OSNI sample ZIP")
            with io.TextIOWrapper(archive.open(entries[0]), encoding="utf-8-sig") as source:
                for x, y, height in iter_osni_xyz(source):
                    count += 1
                    x_values.add(x)
                    y_values.add(y)
                    minimum_height = min(minimum_height, height)
                    maximum_height = max(maximum_height, height)
    except BadZipFile as exc:
        raise RasterFormatError("OSNI sample ZIP is damaged") from exc
    if not count:
        raise RasterFormatError("OSNI sample has no elevation points")
    print(f"points={count}")
    print(f"x={min(x_values):.1f}..{max(x_values):.1f}; y={min(y_values):.1f}..{max(y_values):.1f}")
    print(f"height={minimum_height:.2f}..{maximum_height:.2f} m Belfast Lough MSL")
    for axis, values in (("x", sorted(x_values)), ("y", sorted(y_values))):
        step = min((right - left for left, right in pairwise(values)), default=0.0)
        print(f"minimum {axis} step={step:g} m")
    print("horizontal CRS=UNVERIFIED; no georeferenced output created")


if __name__ == "__main__":
    main()
