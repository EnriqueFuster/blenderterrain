"""Reproject cached geographic RGBNIR windows into Blender-ready PNG tiles."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from ..errors import JobCancelled, NoCoverageError
from ..io.imagery_window import ImageryWindowReader
from ..io.png_validation import validate_png, write_rgb_png
from ..models import ProjectedBounds
from .crs import CRSInfo
from .imagery import plan_texture_tiles
from .planning import ImportPlan
from .projection import project_arrays_to_wgs84

_WORLDCOVER_BLACK_REFLECTANCE = 0.02
_WORLDCOVER_WHITE_REFLECTANCE = 0.40


@dataclass(frozen=True, slots=True)
class ProcessedImageryTile:
    path: Path
    bounds: ProjectedBounds
    width: int
    height: int
    gsd_metres: float


def process_worldcover_imagery(
    source_paths: tuple[Path, ...],
    plan: ImportPlan,
    output_directory: Path,
    progress_callback: Callable[[int, int], None] | None = None,
    cancellation_requested: Callable[[], bool] = lambda: False,
) -> tuple[ProcessedImageryTile, ...]:
    """Preserve the public WorldCover entry point and its cache filenames."""

    return process_imagery_windows(
        source_paths,
        plan,
        output_directory,
        "worldcover_v2",
        progress_callback,
        cancellation_requested,
    )


def process_imagery_windows(
    source_paths: tuple[Path, ...],
    plan: ImportPlan,
    output_directory: Path,
    filename_prefix: str,
    progress_callback: Callable[[int, int], None] | None = None,
    cancellation_requested: Callable[[], bool] = lambda: False,
) -> tuple[ProcessedImageryTile, ...]:
    """Reproject cached reflectance windows onto planned texture grids."""

    readers = tuple(ImageryWindowReader(path) for path in source_paths)
    requests = plan_texture_tiles(plan, filename_prefix)
    outputs: list[ProcessedImageryTile] = []
    if progress_callback is not None:
        progress_callback(0, len(requests))
    for completed, request in enumerate(requests, start=1):
        if cancellation_requested():
            raise JobCancelled("Imagery processing was cancelled")
        path = output_directory / request.filename
        if path.is_file():
            validate_png(path, request.width, request.height)
        else:
            try:
                pixels = _reproject_request(
                    readers,
                    request.bounds,
                    request.width,
                    request.height,
                    plan.work_areas[request.zone_index].crs,
                )
            except NoCoverageError:
                if progress_callback is not None:
                    progress_callback(completed, len(requests))
                continue
            write_rgb_png(path, pixels)
        outputs.append(
            ProcessedImageryTile(
                path, request.bounds, request.width, request.height, request.gsd_metres
            )
        )
        if progress_callback is not None:
            progress_callback(completed, len(requests))
    if not outputs:
        raise NoCoverageError("Imagery source has no usable pixels for the texture grid")
    return tuple(outputs)


def _reproject_request(
    readers: tuple[ImageryWindowReader, ...],
    bounds: ProjectedBounds,
    width: int,
    height: int,
    crs: CRSInfo,
) -> NDArray[np.uint8]:
    output = np.zeros((height, width, 3), dtype=np.uint8)
    covered = np.zeros((height, width), dtype=np.bool_)
    eastings = bounds.west + (np.arange(width, dtype=np.float64) + 0.5) * (
        (bounds.east - bounds.west) / width
    )
    block_rows = 256
    for start in range(0, height, block_rows):
        stop = min(height, start + block_rows)
        northings = bounds.north - (np.arange(start, stop, dtype=np.float64) + 0.5) * (
            (bounds.north - bounds.south) / height
        )
        x, y = np.meshgrid(eastings, northings)
        longitude, latitude = project_arrays_to_wgs84(x, y, crs)
        block = output[start:stop]
        block_covered = covered[start:stop]
        for reader in readers:
            geo = reader.georeference
            source_x, source_y = _source_coordinates(longitude, latitude, geo.epsg)
            columns = np.rint((source_x - geo.origin_x) / geo.pixel_width - 0.5).astype(int)
            rows = np.rint((geo.origin_y - source_y) / -geo.pixel_height - 0.5).astype(int)
            valid = (
                (rows >= 0)
                & (columns >= 0)
                & (rows < reader.data.shape[0])
                & (columns < reader.data.shape[1])
                & ~block_covered
            )
            if not valid.any():
                continue
            samples = np.asarray(reader.data[rows[valid], columns[valid]], dtype=np.float32)
            bands = reader.metadata.bands
            rgb_indices = tuple(bands.index(name) for name in ("B04", "B03", "B02"))
            rgb_linear = samples[:, rgb_indices]
            source_valid = np.any(rgb_linear != reader.metadata.nodata, axis=1)
            if "SCL" in bands:
                source_valid &= ~np.isin(
                    np.rint(samples[:, bands.index("SCL")]).astype(np.int16),
                    (0, 1, 3, 8, 9, 10, 11),
                )
            if not source_valid.any():
                continue
            locations = np.flatnonzero(valid)[source_valid]
            block.reshape(-1, 3)[locations] = _worldcover_rgb(rgb_linear[source_valid])
            block_covered.reshape(-1)[locations] = True
    if not covered.any():
        raise NoCoverageError("WorldCover does not cover this texture tile")
    return output


def _source_coordinates(
    longitude: NDArray[np.float64], latitude: NDArray[np.float64], epsg: int
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    if epsg == 4326:
        return longitude, latitude
    from pyproj import Transformer

    transformer = Transformer.from_crs(4326, epsg, always_xy=True)
    x, y = transformer.transform(longitude, latitude)
    return np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)


def _worldcover_rgb(reflectance: NDArray[np.float32]) -> NDArray[np.uint8]:
    """Apply one fixed natural-colour stretch without introducing tile seams."""

    normalized = (reflectance - _WORLDCOVER_BLACK_REFLECTANCE) / (
        _WORLDCOVER_WHITE_REFLECTANCE - _WORLDCOVER_BLACK_REFLECTANCE
    )
    srgb = np.power(np.clip(normalized, 0.0, 1.0), 1.0 / 2.2)
    return np.rint(srgb * 255.0).astype(np.uint8)
