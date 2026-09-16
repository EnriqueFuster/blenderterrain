"""Read verified historic Scottish LiDAR raster assets by HTTP Range."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from pathlib import Path

from ..catalog import (
    Catalog,
    CoverageMatch,
    DatasetKind,
    LayerRequest,
    ProductRecord,
    ProductSelection,
    load_bundled_catalog,
)
from ..core.acquisition import AcquiredRasterLayer
from ..core.delivery import TransferProgress
from ..core.roi import BBoxWGS84
from ..errors import DownloadIntegrityError, NoCoverageError
from ..io.bigtiff_tiles import BigTiffFloatTileReader, open_float_tile_reader
from ..io.random_access import HttpRangeReader
from .british_cog import acquire_bng_windows
from .british_grid import BritishGridTransform, bundled_ostn15_path

_VERIFIED_PRODUCTS = frozenset(
    {"GB_SCT_SRSP_PHASE1_NH24_DTM", "GB_SCT_SRSP_PHASE1_NH24_DSM"}
)
_TILE_ID = re.compile(r"[A-HJ-Z]{2}\d{2}")
_S3_BASE = "https://srsp-open-data.s3.eu-west-2.amazonaws.com/lidar/phase-1"


def phase1_tile_ids_for_bng_bounds(
    west: float, south: float, east: float, north: float
) -> tuple[str, ...]:
    """Return 10 km British National Grid tile identifiers intersecting bounds."""

    if not all(math.isfinite(value) for value in (west, south, east, north)):
        raise ValueError("British grid bounds must be finite")
    if west >= east or south >= north:
        raise ValueError("British grid bounds must have positive area")
    if west < 0 or south < 0 or east > 700_000 or north > 1_300_000:
        raise NoCoverageError("Bounds fall outside the British National Grid")
    first_easting = math.floor(west / 10_000) * 10_000
    last_easting = math.floor(math.nextafter(east, -math.inf) / 10_000) * 10_000
    first_northing = math.floor(south / 10_000) * 10_000
    last_northing = math.floor(math.nextafter(north, -math.inf) / 10_000) * 10_000
    return tuple(
        _bng_tile_id(easting, northing)
        for northing in range(first_northing, last_northing + 1, 10_000)
        for easting in range(first_easting, last_easting + 1, 10_000)
    )


def phase1_tile_url(kind: DatasetKind, tile_id: str) -> str:
    """Build the official phase-1 raster URL for one validated grid identifier."""

    if kind not in (DatasetKind.DTM, DatasetKind.DSM) or not _TILE_ID.fullmatch(tile_id):
        raise ValueError("Expected a phase-1 DTM/DSM tile identifier")
    label = kind.value
    return f"{_S3_BASE}/{label}/27700/gridded/{tile_id}_1M_{label.upper()}_PHASE1.tif"


def _bng_tile_id(easting: int, northing: int) -> str:
    e100km, n100km = easting // 100_000, northing // 100_000
    first = (19 - n100km) - (19 - n100km) % 5 + (e100km + 10) // 5
    second = ((19 - n100km) * 5) % 25 + e100km % 5
    if first > 7:
        first += 1
    if second > 7:
        second += 1
    return (
        f"{chr(ord('A') + first)}{chr(ord('A') + second)}"
        f"{easting % 100_000 // 10_000}{northing % 100_000 // 10_000}"
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
        if product.coverage.match(roi) is CoverageMatch.NONE:
            raise NoCoverageError("NH24 does not intersect the requested ROI")
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
