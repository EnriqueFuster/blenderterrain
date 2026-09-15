"""Bounded native-grid access to the official Welsh LiDAR mosaics."""

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
        return acquire_bng_windows(
            product,
            selection,
            roi,
            cache_directory,
            BritishGridTransform(self.grid_path),
            open_wales_reader,
            "wales-v1",
            progress_callback,
            cancellation_requested,
        )
