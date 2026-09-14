"""Bounded native-grid access to the official Welsh LiDAR mosaics."""

import hashlib
import math
from collections.abc import Callable
from pathlib import Path

import numpy as np

from ..catalog import (
    Catalog,
    DatasetKind,
    LayerRequest,
    ProductRecord,
    ProductSelection,
    load_bundled_catalog,
)
from ..core.acquisition import AcquiredRasterLayer
from ..core.delivery import TransferProgress
from ..core.roi import BBoxWGS84
from ..errors import DownloadIntegrityError, JobCancelled
from ..io.bigtiff_tiles import BigTiffFloatTileReader, open_float_tile_reader
from ..io.elevation_window import elevation_window_is_valid, write_elevation_window
from ..io.random_access import HttpRangeReader
from ..models import ProjectedBounds
from .british_grid import BritishGridTransform, bundled_ostn15_path


def open_wales_reader(product: ProductRecord, cache_directory: Path) -> BigTiffFloatTileReader:
    """Open a 1 m BNG mosaic by HTTP Range; never fall back to full download."""

    if product.provider_id != "datamap_wales" or product.capabilities.kind not in (
        DatasetKind.DTM,
        DatasetKind.DSM,
    ):
        raise ValueError("Expected a DataMapWales elevation product")
    source = HttpRangeReader(
        product.endpoint,
        cache_directory,
        allowed_hosts=frozenset({"dmwproductionblob.blob.core.windows.net"}),
        maximum_source_bytes=100_000_000_000,
    )
    reader = open_float_tile_reader(source)
    reference = reader.georeference
    if (
        reference.epsg != 27700
        or reference.pixel_width != 1.0
        or reference.pixel_height != -1.0
        or reader.nodata != -9999.0
    ):
        raise DownloadIntegrityError("DataMapWales raster CRS, resolution or NoData changed")
    return reader


class DataMapWalesAcquirer:
    """Read bounded BNG windows and publish geographic windows for the common pipeline."""

    def __init__(self, catalog: Catalog | None = None, grid_path: Path | None = None) -> None:
        self.catalog = catalog or load_bundled_catalog()
        self.grid_path = grid_path or bundled_ostn15_path()

    def acquire(
        self,
        selection: ProductSelection,
        request: LayerRequest,
        roi: BBoxWGS84,
        cache_directory: Path,
        progress_callback: Callable[[TransferProgress], None] | None = None,
        cancellation_requested: Callable[[], bool] = lambda: False,
    ) -> AcquiredRasterLayer:
        product = self.catalog.product(selection.product_id)
        if (
            selection.provider_id != "datamap_wales"
            or product.provider_id != selection.provider_id
            or selection.kind is not request.kind
            or product.capabilities.kind is not selection.kind
        ):
            raise ValueError("DataMapWales received an incompatible selection")
        transform = BritishGridTransform(self.grid_path)
        # Preserve native samples; the common processing stage applies the requested GSD.
        # Coarse output requests must not turn a 512-cell read into a huge native window.
        resolution = 1.0
        width = max(
            2,
            math.ceil(
                roi.longitude_span * 111_320 * math.cos(math.radians(roi.south)) / resolution
            ),
        )
        height = max(2, math.ceil(roi.latitude_span * 111_320 / resolution))
        dx, dy = roi.longitude_span / width, roi.latitude_span / height
        key = hashlib.sha256(f"wales-v1|{roi}|{resolution}".encode()).hexdigest()[:20]
        target = cache_directory / selection.provider_id / product.id / key
        total = math.ceil(width / 512) * math.ceil(height / 512)
        paths: list[Path] = []
        cached_count = 0
        reader = None
        for row in range(0, height, 512):
            for column in range(0, width, 512):
                if cancellation_requested():
                    raise JobCancelled("DataMapWales acquisition was cancelled")
                # Avoid one-pixel final windows, unsupported by the portable artifact format.
                h, w = min(512, height - row), min(512, width - column)
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
                        reader = open_wales_reader(product, target / "ranges")
                    x, y = np.meshgrid(
                        bounds.west + (np.arange(w) + 0.5) * dx,
                        bounds.north - (np.arange(h) + 0.5) * dy,
                    )
                    east, north = transform.forward(x, y)
                    reference = reader.georeference
                    columns = np.floor((east - reference.origin_x) / reference.pixel_width).astype(
                        int
                    )
                    rows = np.floor((north - reference.origin_y) / reference.pixel_height).astype(
                        int
                    )
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
        return AcquiredRasterLayer(
            selection.provider_id, product.id, selection.kind, tuple(paths), cached_count
        )
