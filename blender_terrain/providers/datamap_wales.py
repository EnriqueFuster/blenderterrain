"""Bounded native-grid access to the official Welsh LiDAR mosaics."""

from pathlib import Path

from ..catalog import DatasetKind, ProductRecord
from ..errors import DownloadIntegrityError
from ..io.bigtiff_tiles import BigTiffFloatTileReader, open_float_tile_reader
from ..io.random_access import HttpRangeReader


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
