"""Parse OSNI DTM sample coordinates without assuming a horizontal CRS."""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator

from ..errors import RasterFormatError


def iter_osni_xyz(lines: Iterable[str]) -> Iterator[tuple[float, float, float]]:
    """Yield finite x/y/height rows after the observed three-column header."""

    iterator = enumerate(lines, start=1)
    for _line_number, line in iterator:
        if line.strip():
            if line.split() != ["x", "y", "z"]:
                raise RasterFormatError("OSNI sample must start with an x y z header")
            break
    else:
        raise RasterFormatError("OSNI sample contains no x y z header")

    for line_number, line in iterator:
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 3:
            raise RasterFormatError(f"OSNI sample line {line_number} has no x y z triple")
        try:
            point = (float(fields[0]), float(fields[1]), float(fields[2]))
        except ValueError as exc:
            raise RasterFormatError(f"OSNI sample line {line_number} is not numeric") from exc
        if not all(math.isfinite(value) for value in point):
            raise RasterFormatError(f"OSNI sample line {line_number} is not finite")
        yield point
