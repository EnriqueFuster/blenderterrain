"""Environment Agency elevation requests for England."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

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
from ..io.bigtiff_tiles import open_float_tile_reader
from ..io.http_download import DownloadedAsset, download_public_tiff

PROVIDER_ID = "environment_agency"
_EARTH_RADIUS_METRES = 6_371_008.8


@dataclass(frozen=True, slots=True)
class EnvironmentAgencyRequest:
    """One bounded native-resolution WCS coverage request."""

    row: int
    column: int
    bounds: BBoxWGS84
    width: int
    height: int
    maximum_dimension: int


def plan_environment_agency_requests(
    roi: BBoxWGS84,
    maximum_dimension: int,
    resolution_metres: float = 1.0,
) -> tuple[EnvironmentAgencyRequest, ...]:
    """Split a geographic ROI into bounded native-resolution WCS requests."""

    if maximum_dimension <= 8 or resolution_metres <= 0:
        raise ValueError("Environment Agency WCS request limits are invalid")
    latitude_for_width = min((abs(roi.south), abs(roi.north)))
    width_metres = (
        math.radians(roi.longitude_span)
        * _EARTH_RADIUS_METRES
        * math.cos(math.radians(latitude_for_width))
    )
    height_metres = math.radians(roi.latitude_span) * _EARTH_RADIUS_METRES
    safe_dimension = maximum_dimension - 4
    column_parts = max(1, math.ceil(width_metres / resolution_metres / safe_dimension))
    row_parts = max(1, math.ceil(height_metres / resolution_metres / safe_dimension))
    requests: list[EnvironmentAgencyRequest] = []
    for row in range(row_parts):
        north = roi.north - roi.latitude_span * row / row_parts
        south = roi.north - roi.latitude_span * (row + 1) / row_parts
        for column in range(column_parts):
            west = roi.west + roi.longitude_span * column / column_parts
            east = roi.west + roi.longitude_span * (column + 1) / column_parts
            requests.append(
                EnvironmentAgencyRequest(
                    row,
                    column,
                    BBoxWGS84(west, south, east, north),
                    math.ceil(width_metres / resolution_metres / column_parts) + 2,
                    math.ceil(height_metres / resolution_metres / row_parts) + 2,
                    maximum_dimension,
                )
            )
    return tuple(requests)


class EnvironmentAgencyWCSClient:
    """Download and validate numeric GeoTIFF windows from one EA product."""

    def __init__(self, product: ProductRecord) -> None:
        if product.provider_id != PROVIDER_ID or product.wcs is None:
            raise ValueError("Environment Agency WCS client received an incompatible product")
        self.product = product

    def request_url(self, request: EnvironmentAgencyRequest) -> str:
        contract = self.product.wcs
        assert contract is not None
        if contract.subsetting_crs_epsg is None or contract.output_crs_epsg is None:
            raise ValueError("Environment Agency WCS request CRS is not configured")
        subsetting_crs = _epsg_uri(contract.subsetting_crs_epsg)
        output_crs = _epsg_uri(contract.output_crs_epsg)
        query = urlencode(
            [
                ("service", "WCS"),
                ("version", contract.version),
                ("request", "GetCoverage"),
                ("coverageId", contract.coverage_id),
                ("subsettingCrs", subsetting_crs),
                ("outputCrs", output_crs),
                ("subset", f"Lat({request.bounds.south:g},{request.bounds.north:g})"),
                ("subset", f"Long({request.bounds.west:g},{request.bounds.east:g})"),
                ("format", contract.format),
            ]
        )
        return f"{self.product.endpoint}?{query}"

    def download(
        self,
        request: EnvironmentAgencyRequest,
        cache_directory: Path,
        progress_callback: Callable[[int, int | None], None] | None = None,
        cancellation_requested: Callable[[], bool] = lambda: False,
    ) -> DownloadedAsset:
        filename = f"ea_r{request.row}_c{request.column}.tif"
        maximum_bytes = max(1024 * 1024, request.width * request.height * 8)
        result = download_public_tiff(
            self.request_url(request),
            cache_directory,
            filename,
            maximum_bytes=maximum_bytes,
            progress_callback=progress_callback,
            cancelled=cancellation_requested,
        )
        self._validate_raster(result.path, request)
        return result

    @staticmethod
    def _validate_raster(path: Path, request: EnvironmentAgencyRequest) -> None:
        reader = open_float_tile_reader(path)
        if max(reader.layout.width, reader.layout.height) > request.maximum_dimension:
            raise DownloadIntegrityError("Environment Agency WCS dimensions exceed the safe limit")
        if reader.georeference.epsg != 4326:
            raise DownloadIntegrityError("Environment Agency WCS CRS changed")
        actual = reader.georeference.bounds(reader.layout.width, reader.layout.height)
        if not (
            actual[0] <= request.bounds.west
            and actual[1] <= request.bounds.south
            and actual[2] >= request.bounds.east
            and actual[3] >= request.bounds.north
        ):
            raise DownloadIntegrityError("Environment Agency WCS bounds changed")


def _epsg_uri(epsg: int) -> str:
    return f"http://www.opengis.net/def/crs/EPSG/0/{epsg}"


class EnvironmentAgencyWCSAcquirer:
    """Acquire a confirmed English DTM or DSM selection."""

    def __init__(self, catalog: Catalog | None = None) -> None:
        self.catalog = catalog or load_bundled_catalog()

    def acquire(
        self,
        selection: ProductSelection,
        request: LayerRequest,
        roi: BBoxWGS84,
        cache_directory: Path,
        progress_callback: Callable[[TransferProgress], None] | None = None,
        cancellation_requested: Callable[[], bool] = lambda: False,
    ) -> AcquiredRasterLayer:
        if (
            selection.provider_id != PROVIDER_ID
            or selection.kind is not request.kind
            or selection.kind not in {DatasetKind.DTM, DatasetKind.DSM}
        ):
            raise ValueError("Environment Agency acquirer received an incompatible selection")
        product = self.catalog.product(selection.product_id)
        if (
            product.provider_id != PROVIDER_ID
            or product.capabilities.kind is not selection.kind
            or product.wcs is None
        ):
            raise ValueError("Environment Agency acquirer received an incompatible selection")
        contract = product.wcs
        windows = plan_environment_agency_requests(
            roi,
            contract.maximum_dimension,
            product.capabilities.native_resolution_m,
        )
        target = cache_directory / PROVIDER_ID / product.id
        client = EnvironmentAgencyWCSClient(product)
        paths: list[Path] = []
        cached_count = 0
        for index, window in enumerate(windows):
            if cancellation_requested():
                raise JobCancelled("Environment Agency acquisition was cancelled")

            def report(written: int, expected: int | None, *, _index: int = index) -> None:
                if progress_callback is not None:
                    progress_callback(
                        TransferProgress(
                            selection.kind.value,
                            _index,
                            len(windows),
                            f"WCS block {_index + 1}/{len(windows)}",
                            written,
                            expected,
                        )
                    )

            result = client.download(window, target, report, cancellation_requested)
            paths.append(result.path)
            cached_count += int(result.cached)
            if result.cached and progress_callback is not None:
                progress_callback(
                    TransferProgress(
                        selection.kind.value,
                        index,
                        len(windows),
                        f"WCS block {index + 1}/{len(windows)}",
                        result.bytes,
                        result.bytes,
                        cached=True,
                    )
                )
        return AcquiredRasterLayer(
            PROVIDER_ID,
            product.id,
            selection.kind,
            tuple(paths),
            cached_count,
        )
