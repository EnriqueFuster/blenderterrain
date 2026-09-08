"""Environment Agency elevation requests for England."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from ..catalog import ProductRecord
from ..core.grid import GridSpec, tile_grid
from ..errors import DownloadIntegrityError
from ..io.bigtiff_tiles import open_float_tile_reader
from ..io.http_download import DownloadedAsset, download_public_tiff
from ..models import ProjectedBounds

PROVIDER_ID = "environment_agency"


@dataclass(frozen=True, slots=True)
class EnvironmentAgencyRequest:
    """One bounded native-resolution WCS coverage request."""

    row: int
    column: int
    bounds: ProjectedBounds
    width: int
    height: int


def plan_environment_agency_requests(
    grid: GridSpec,
    maximum_dimension: int,
) -> tuple[EnvironmentAgencyRequest, ...]:
    """Split a 1 m British National Grid into deterministic WCS requests."""

    if grid.bounds.epsg != 27700 or not math.isclose(grid.resolution, 1.0):
        raise ValueError("Environment Agency WCS requests require a 1 m EPSG:27700 grid")
    return tuple(
        EnvironmentAgencyRequest(
            tile.row,
            tile.column,
            tile.bounds,
            tile.columns,
            tile.rows,
        )
        for tile in tile_grid(grid, maximum_dimension)
    )


class EnvironmentAgencyWCSClient:
    """Download and validate numeric GeoTIFF windows from one EA product."""

    def __init__(self, product: ProductRecord) -> None:
        if product.provider_id != PROVIDER_ID or product.wcs is None:
            raise ValueError("Environment Agency WCS client received an incompatible product")
        self.product = product

    def request_url(self, request: EnvironmentAgencyRequest) -> str:
        contract = self.product.wcs
        assert contract is not None
        query = urlencode(
            [
                ("service", "WCS"),
                ("version", contract.version),
                ("request", "GetCoverage"),
                ("coverageId", contract.coverage_id),
                ("subset", f"E({request.bounds.west:g},{request.bounds.east:g})"),
                ("subset", f"N({request.bounds.south:g},{request.bounds.north:g})"),
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
        if reader.layout.width != request.width or reader.layout.height != request.height:
            raise DownloadIntegrityError("Environment Agency WCS dimensions changed")
        if reader.georeference.epsg != 27700:
            raise DownloadIntegrityError("Environment Agency WCS CRS changed")
        actual = reader.georeference.bounds(reader.layout.width, reader.layout.height)
        expected = (
            request.bounds.west,
            request.bounds.south,
            request.bounds.east,
            request.bounds.north,
        )
        if not all(
            math.isclose(a, b, abs_tol=1e-6)
            for a, b in zip(actual, expected, strict=True)
        ):
            raise DownloadIntegrityError("Environment Agency WCS bounds changed")
