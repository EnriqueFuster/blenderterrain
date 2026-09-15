"""Read verified historic Scottish LiDAR raster assets by HTTP Range."""

from __future__ import annotations

from pathlib import Path

from ..catalog import DatasetKind, ProductRecord
from ..errors import DownloadIntegrityError
from ..io.bigtiff_tiles import BigTiffFloatTileReader, open_float_tile_reader
from ..io.random_access import HttpRangeReader


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
