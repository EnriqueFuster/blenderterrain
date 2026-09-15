"""Read verified historic Scottish LiDAR raster assets by HTTP Range."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

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
from ..errors import DownloadIntegrityError
from ..io.bigtiff_tiles import BigTiffFloatTileReader, open_float_tile_reader
from ..io.random_access import HttpRangeReader
from .british_cog import acquire_bng_windows
from .british_grid import BritishGridTransform, bundled_ostn15_path

_VERIFIED_PRODUCTS = frozenset(
    {"GB_SCT_SRSP_PHASE1_NH24_DTM", "GB_SCT_SRSP_PHASE1_NH24_DSM"}
)


def open_scottish_lidar_reader(
    product: ProductRecord, cache_directory: Path
) -> BigTiffFloatTileReader:
    """Open one historic phase-1 Float32 raster; never download the full asset."""

    if (
        product.provider_id != "scottish_remote_sensing"
        or product.capabilities.kind not in (DatasetKind.DTM, DatasetKind.DSM)
        or not product.endpoint.startswith(
            "https://srsp-open-data.s3.eu-west-2.amazonaws.com/lidar/phase-1/"
        )
    ):
        raise ValueError("Expected a verified Scottish LiDAR phase-1 raster")
    source = HttpRangeReader(
        product.endpoint,
        cache_directory,
        allowed_hosts=frozenset({"srsp-open-data.s3.eu-west-2.amazonaws.com"}),
        maximum_source_bytes=2_000_000_000,
    )
    reader = open_float_tile_reader(source)
    reference = reader.georeference
    if (
        reference.epsg != 27700
        or abs(reference.pixel_width - 1.0) > 0.001
        or abs(reference.pixel_height + 1.0) > 0.001
        or reader.nodata != -9999.0
    ):
        raise DownloadIntegrityError("Scottish LiDAR asset grid or NoData changed")
    return reader


class ScottishLidarAcquirer:
    """Extract one verified phase-1 NH24 asset without selecting other campaigns."""

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
            product.id not in _VERIFIED_PRODUCTS
            or selection.provider_id != "scottish_remote_sensing"
            or product.provider_id != selection.provider_id
            or selection.kind is not request.kind
            or product.capabilities.kind is not selection.kind
        ):
            raise ValueError("Scottish LiDAR received an unverified or incompatible selection")
        return acquire_bng_windows(
            product,
            selection,
            roi,
            cache_directory,
            BritishGridTransform(self.grid_path),
            open_scottish_lidar_reader,
            "scotland-nh24-v1",
            progress_callback,
            cancellation_requested,
        )
