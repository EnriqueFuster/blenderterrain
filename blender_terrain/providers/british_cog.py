"""Shared bounded BNG-to-geographic window extraction for British COG sources."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from ..catalog import ProductRecord, ProductSelection
from ..core.acquisition import AcquiredRasterLayer
from ..core.delivery import TransferProgress
from ..core.roi import BBoxWGS84
from ..errors import JobCancelled, NoCoverageError
from ..io.bigtiff_tiles import GeoReference, TileLayout
from ..io.elevation_window import (
    ElevationWindowReader,
    elevation_window_is_valid,
    write_elevation_window,
)
from ..models import ProjectedBounds
from .british_grid import BritishGridTransform


class BngWindowReader(Protocol):
    @property
    def georeference(self) -> GeoReference: ...

    @property
    def layout(self) -> TileLayout: ...

    @property
    def nodata(self) -> float: ...

    def read_window(
        self, row: int, column: int, height: int, width: int
    ) -> NDArray[np.float32]: ...


ReaderFactory = Callable[[ProductRecord, Path], BngWindowReader]


def acquire_bng_windows(
    product: ProductRecord,
    selection: ProductSelection,
    roi: BBoxWGS84,
    cache_directory: Path,
    transform: BritishGridTransform,
    reader_factory: ReaderFactory,
    cache_version: str,
    progress_callback: Callable[[TransferProgress], None] | None,
    cancellation_requested: Callable[[], bool],
) -> AcquiredRasterLayer:
    """Read only intersecting native cells and publish bounded EPSG:4326 artifacts."""

    width = max(
        2,
        math.ceil(roi.longitude_span * 111_320 * math.cos(math.radians(roi.south))),
    )
    height = max(2, math.ceil(roi.latitude_span * 111_320))
    dx, dy = roi.longitude_span / width, roi.latitude_span / height
    key = hashlib.sha256(f"{cache_version}|{roi}|1.0".encode()).hexdigest()[:20]
    target = cache_directory / selection.provider_id / product.id / key
    total = math.ceil(width / 512) * math.ceil(height / 512)
    paths: list[Path] = []
    cached_count = 0
    reader = None
    has_data = False
    for row in range(0, height, 512):
        for column in range(0, width, 512):
            if cancellation_requested():
                raise JobCancelled(f"{product.provider_id} acquisition was cancelled")
            h, w = min(512, height - row), min(512, width - column)
            # A final single row/column overlaps the previous one by one cell.
            if h == 1:
                row -= 1
                h = 2
            if w == 1:
                column -= 1
                w = 2
            bounds = ProjectedBounds(
                roi.west + column * dx,
                roi.north - (row + h) * dy,
                roi.west + (column + w) * dx,
                roi.north - row * dy,
                4326,
            )
            path = target / f"r{row}_c{column}.npy"
            cached = elevation_window_is_valid(path)
            if not cached:
                if reader is None:
                    reader = reader_factory(product, target / "ranges")
                x, y = np.meshgrid(
                    bounds.west + (np.arange(w) + 0.5) * dx,
                    bounds.north - (np.arange(h) + 0.5) * dy,
                )
                east, north = transform.forward(x, y)
                reference = reader.georeference
                columns = np.floor((east - reference.origin_x) / reference.pixel_width).astype(int)
                rows = np.floor((north - reference.origin_y) / reference.pixel_height).astype(int)
                valid = (
                    (rows >= 0)
                    & (columns >= 0)
                    & (rows < reader.layout.height)
                    & (columns < reader.layout.width)
                )
                data = np.full((h, w), reader.nodata, dtype=np.float32)
                if np.any(valid):
                    top, left = int(rows[valid].min()), int(columns[valid].min())
                    source = reader.read_window(
                        top,
                        left,
                        int(rows[valid].max()) - top + 1,
                        int(columns[valid].max()) - left + 1,
                    )
                    data[valid] = source[rows[valid] - top, columns[valid] - left]
                has_data |= bool(np.any(data != reader.nodata))
                path.unlink(missing_ok=True)
                path.with_suffix(".npy.json").unlink(missing_ok=True)
                write_elevation_window(path, data, bounds, reader.nodata)
            paths.append(path)
            cached_count += int(cached)
            if progress_callback is not None:
                size = path.stat().st_size
                progress_callback(
                    TransferProgress(
                        selection.kind.value,
                        len(paths) - 1,
                        total,
                        path.name,
                        size,
                        size,
                        cached=cached,
                    )
                )
    if not has_data:
        # Cached windows can be reused without opening the remote raster again.
        for path in paths:
            window = ElevationWindowReader(path)
            if np.any(np.load(path, mmap_mode="r", allow_pickle=False) != window.nodata):
                has_data = True
                break
    if not has_data:
        raise NoCoverageError(f"{product.name} has no elevation cells in the ROI")
    return AcquiredRasterLayer(
        selection.provider_id, product.id, selection.kind, tuple(paths), cached_count
    )
