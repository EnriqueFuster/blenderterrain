"""Validate a georeferenced local PNG image for terrain texturing."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from ..errors import UserInputError
from ..io.png_validation import read_png_dimensions, validate_png
from ..models import ProjectedBounds


@dataclass(frozen=True, slots=True)
class LocalImageryInspection:
    """Validated local image metadata ready for a delivery result."""

    path: Path
    bounds: ProjectedBounds
    width: int
    height: int
    gsd_metres: float


def inspect_local_imagery(path: Path) -> LocalImageryInspection:
    """Validate PNG pixels plus a same-name world file and projected .prj file."""

    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.suffix.lower() != ".png":
        raise UserInputError("Local imagery must be a PNG file")
    width, height = read_png_dimensions(resolved)
    validate_png(resolved, width, height)
    world_path = _sidecar(resolved, {".pgw", ".wld"})
    projection_path = _sidecar(resolved, {".prj"})
    if world_path is None:
        raise UserInputError("Local PNG imagery requires a same-name .pgw or .wld file")
    if projection_path is None:
        raise UserInputError("Local PNG imagery requires a same-name .prj file")
    values = _world_values(world_path)
    pixel_x, rotation_y, rotation_x, pixel_y, center_x, center_y = values
    if not math.isclose(rotation_x, 0.0, abs_tol=1e-12) or not math.isclose(
        rotation_y, 0.0, abs_tol=1e-12
    ):
        raise UserInputError("Rotated local imagery world files are not supported")
    if pixel_x <= 0.0 or pixel_y >= 0.0 or not math.isclose(
        pixel_x, -pixel_y, rel_tol=1e-9, abs_tol=1e-9
    ):
        raise UserInputError("Local imagery must use square north-up pixels")
    epsg = _projected_epsg(projection_path)
    west = center_x - pixel_x / 2.0
    north = center_y - pixel_y / 2.0
    bounds = ProjectedBounds(
        west,
        north + height * pixel_y,
        west + width * pixel_x,
        north,
        epsg,
    )
    return LocalImageryInspection(resolved, bounds, width, height, pixel_x)


def _sidecar(path: Path, suffixes: set[str]) -> Path | None:
    for candidate in path.parent.iterdir():
        if (
            candidate.is_file()
            and candidate.stem.casefold() == path.stem.casefold()
            and candidate.suffix.lower() in suffixes
        ):
            return candidate
    return None


def _world_values(path: Path) -> tuple[float, float, float, float, float, float]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        values = tuple(float(line.strip()) for line in lines if line.strip())
    except (OSError, UnicodeError, ValueError) as exc:
        raise UserInputError("Local imagery world file is not valid numeric text") from exc
    if len(values) != 6 or not all(math.isfinite(value) for value in values):
        raise UserInputError("Local imagery world file must contain six finite numbers")
    return values


def _projected_epsg(path: Path) -> int:
    try:
        wkt = path.read_text(encoding="utf-8-sig").upper()
    except (OSError, UnicodeError) as exc:
        raise UserInputError("Local imagery projection file cannot be read") from exc
    codes = {
        int(value)
        for value in re.findall(
            r'(?:AUTHORITY|ID)\s*\[\s*["\']EPSG["\']\s*,\s*["\']?(\d+)', wkt
        )
    }
    canonical = {3040: 25828, 3041: 25829, 3042: 25830, 3043: 25831}
    supported = {4083, 25829, 25830, 25831, *canonical}
    selected = next((code for code in codes if code in supported), None)
    if selected is None:
        raise UserInputError(
            "Local imagery CRS must be EPSG:4083 or ETRS89 UTM 25829-25831"
        )
    return canonical.get(selected, selected)
