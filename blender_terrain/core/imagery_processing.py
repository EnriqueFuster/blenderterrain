"""Reproject cached geographic RGBNIR windows into Blender-ready PNG tiles."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from ..errors import JobCancelled, RasterFormatError
from ..io.imagery_window import ImageryWindowReader
from ..io.png_validation import validate_png, write_rgb_png
from ..models import ProjectedBounds
from .crs import CRSInfo
from .imagery import plan_imagery_tiles
from .planning import ImportPlan
from .projection import project_utm_arrays_to_wgs84


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
    """Nearest-neighbour reproject RGB bands onto the planned UTM texture grids."""

    readers = tuple(ImageryWindowReader(path) for path in source_paths)
    requests = plan_imagery_tiles(plan)
    outputs: list[ProcessedImageryTile] = []
    if progress_callback is not None:
        progress_callback(0, len(requests))
    for request in requests:
        if cancellation_requested():
            raise JobCancelled("Imagery processing was cancelled")
        path = output_directory / request.filename.replace("pnoa_", "worldcover_")
        if path.is_file():
            validate_png(path, request.width, request.height)
        else:
            pixels = _reproject_request(
                readers,
                request.bounds,
                request.width,
                request.height,
                plan.work_areas[request.zone_index].crs,
            )
            write_rgb_png(path, pixels)
        outputs.append(
            ProcessedImageryTile(
                path, request.bounds, request.width, request.height, request.gsd_metres
            )
        )
        if progress_callback is not None:
            progress_callback(len(outputs), len(requests))
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
        longitude, latitude = project_utm_arrays_to_wgs84(x, y, crs)
        block = output[start:stop]
        block_covered = covered[start:stop]
        for reader in readers:
            geo = reader.georeference
            columns = np.rint((longitude - geo.origin_x) / geo.pixel_width - 0.5).astype(int)
            rows = np.rint((geo.origin_y - latitude) / -geo.pixel_height - 0.5).astype(int)
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
            source_valid = np.any(samples != reader.metadata.nodata, axis=1)
            if not source_valid.any():
                continue
            locations = np.flatnonzero(valid)[source_valid]
            rgb_linear = samples[source_valid][:, (2, 1, 0)]
            rgb = np.power(np.clip(rgb_linear / 0.3, 0.0, 1.0), 1.0 / 2.2)
            block.reshape(-1, 3)[locations] = np.rint(rgb * 255.0).astype(np.uint8)
            block_covered.reshape(-1)[locations] = True
    if not covered.all():
        raise RasterFormatError("WorldCover imagery does not fully cover the texture tile")
    return output
