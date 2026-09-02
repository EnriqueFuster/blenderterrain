"""Bounded WMS elevation acquisition from the French Géoplateforme."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request

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
from ..core.crs import work_area_for_crs
from ..core.delivery import TransferProgress
from ..core.grid import GridSpec, align_projected_grid, tile_grid
from ..core.projection import project_work_area_bounds
from ..core.roi import BBoxWGS84
from ..errors import (
    DownloadIntegrityError,
    JobCancelled,
    ProviderContractChanged,
    ProviderUnavailableError,
)
from ..io.atomic import finalize_part, safe_destination
from ..io.bil32 import (
    Bil32Metadata,
    Bil32WindowReader,
    validate_bil32_file,
    write_bil32_metadata,
)
from ..io.png_validation import validate_png
from ..io.wms_capabilities import WMSCapabilities, parse_wms_capabilities
from ..io.wms_download import WMSOpener, build_wms_opener, download_wms_response
from ..models import ProjectedBounds

PROVIDER_ID = "ign_france"
TRUSTED_HOST = "data.geopf.fr"
CAPABILITIES_MAXIMUM_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class GeopfElevationRequest:
    """One node-aligned WMS BIL request derived from a master terrain grid."""

    bounds: ProjectedBounds
    width: int
    height: int
    row: int
    column: int


def plan_geopf_elevation_requests(
    grid: GridSpec, maximum_dimension: int
) -> tuple[GeopfElevationRequest, ...]:
    """Split a terrain grid into WMS windows sharing their boundary nodes."""

    if maximum_dimension < 2:
        raise ValueError("WMS maximum dimension must be at least two pixels")
    maximum_cells = maximum_dimension - 1
    requests: list[GeopfElevationRequest] = []
    for row_offset in range(0, grid.rows, maximum_cells):
        rows = min(maximum_cells, grid.rows - row_offset)
        north = grid.bounds.north - row_offset * grid.resolution
        south = north - rows * grid.resolution
        for column_offset in range(0, grid.columns, maximum_cells):
            columns = min(maximum_cells, grid.columns - column_offset)
            west = grid.bounds.west + column_offset * grid.resolution
            east = west + columns * grid.resolution
            half = grid.resolution / 2.0
            requests.append(
                GeopfElevationRequest(
                    ProjectedBounds(
                        west - half,
                        south - half,
                        east + half,
                        north + half,
                        grid.bounds.epsg,
                    ),
                    columns + 1,
                    rows + 1,
                    row_offset,
                    column_offset,
                )
            )
    return tuple(requests)


class GeopfWMSClient:
    """Download and cache validated BIL32 windows for one catalog product."""

    def __init__(
        self,
        product: ProductRecord,
        *,
        timeout_seconds: float = 30.0,
        retries: int = 2,
        opener: WMSOpener | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if product.provider_id != PROVIDER_ID or product.wms is None:
            raise ValueError("Géoplateforme client requires a French WMS product")
        if timeout_seconds <= 0 or retries < 0:
            raise ValueError("WMS timeout and retries are invalid")
        if urlsplit(product.endpoint).hostname != TRUSTED_HOST:
            raise ValueError("French WMS endpoint is not allow-listed")
        self.product = product
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.opener = opener or build_wms_opener()
        self.sleeper = sleeper
        self._capabilities: WMSCapabilities | None = None

    def capabilities(self) -> WMSCapabilities:
        if self._capabilities is not None:
            return self._capabilities
        contract = self.product.wms
        assert contract is not None
        query = urlencode(
            {"SERVICE": "WMS", "VERSION": contract.version, "REQUEST": "GetCapabilities"}
        )
        request = Request(
            f"{self.product.endpoint}?{query}",
            headers={"User-Agent": "BlenderTerrain/0.5"},
        )
        body, content_type = self._read_bounded(request, CAPABILITIES_MAXIMUM_BYTES)
        if content_type not in {"application/xml", "text/xml"}:
            raise ProviderContractChanged("French WMS capabilities did not return XML")
        capabilities = parse_wms_capabilities(body, contract.layer, contract.format)
        if (
            f"EPSG:{contract.crs_epsg}" not in capabilities.crs
            or capabilities.max_width < contract.maximum_dimension
            or capabilities.max_height < contract.maximum_dimension
        ):
            raise ProviderContractChanged("French WMS no longer supports the configured grid")
        self._capabilities = capabilities
        return capabilities

    def download_bil(
        self,
        window: GeopfElevationRequest,
        cache_directory: Path,
        progress_callback: Callable[[int, int | None], None] | None = None,
        cancellation_requested: Callable[[], bool] = lambda: False,
    ) -> tuple[Path, bool]:
        contract = self.product.wms
        assert contract is not None
        if (
            contract.format != "image/x-bil;bits=32"
            or contract.sample_dtype != "<f4"
            or contract.nodata is None
        ):
            raise ValueError("BIL request does not match the French elevation contract")
        nodata = contract.nodata
        if window.bounds.epsg != contract.crs_epsg:
            raise ValueError("WMS request CRS does not match the product contract")
        url = self._map_url(window)
        filename = f"{self.product.id.lower()}_{hashlib.sha256(url.encode()).hexdigest()[:20]}.bil"
        cache_directory.mkdir(parents=True, exist_ok=True)
        destination = safe_destination(cache_directory, filename)
        sidecar = destination.with_suffix(destination.suffix + ".json")
        if destination.exists() and sidecar.exists():
            Bil32WindowReader(destination, verify_hash=True)
            return destination, True
        self.capabilities()
        destination.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        expected_bytes = window.width * window.height * 4
        digests: list[str] = []
        try:
            download_wms_response(
                url,
                cache_directory,
                filename,
                content_type="image/x-bil",
                accept=contract.format,
                maximum_bytes=expected_bytes,
                exact_bytes=expected_bytes,
                timeout_seconds=self.timeout_seconds,
                retries=self.retries,
                validator=lambda path: digests.append(
                    validate_bil32_file(
                        path, window.width, window.height, nodata
                    )
                ),
                progress_callback=progress_callback,
                cancellation_requested=cancellation_requested,
                opener=self.opener,
                sleeper=self.sleeper,
            )
            write_bil32_metadata(
                destination,
                Bil32Metadata(
                    PROVIDER_ID,
                    self.product.id,
                    self.product.endpoint,
                    contract.version,
                    contract.layer,
                    contract.style,
                    contract.format,
                    contract.crs_epsg,
                    (
                        window.bounds.west,
                        window.bounds.south,
                        window.bounds.east,
                        window.bounds.north,
                    ),
                    window.width,
                    window.height,
                    contract.sample_dtype,
                    "north_to_south",
                    nodata,
                    datetime.now(UTC).isoformat(),
                    digests[0],
                ),
            )
            return destination, False
        except BaseException:
            if not sidecar.exists():
                destination.unlink(missing_ok=True)
            raise

    def download_png(
        self,
        bounds: ProjectedBounds,
        width: int,
        height: int,
        cache_directory: Path,
        progress_callback: Callable[[int, int | None], None] | None = None,
        cancellation_requested: Callable[[], bool] = lambda: False,
    ) -> tuple[Path, bool]:
        """Download one projected orthophoto window with a provenance sidecar."""

        contract = self.product.wms
        assert contract is not None
        if contract.format != "image/png" or bounds.epsg != contract.crs_epsg:
            raise ValueError("PNG request does not match the French imagery contract")
        if (
            not 0 < width <= contract.maximum_dimension
            or not 0 < height <= contract.maximum_dimension
        ):
            raise ValueError("PNG dimensions exceed the configured WMS limit")
        url = self._map_url_values(bounds, width, height)
        filename = f"{self.product.id.lower()}_{hashlib.sha256(url.encode()).hexdigest()[:20]}.png"
        cache_directory.mkdir(parents=True, exist_ok=True)
        destination = safe_destination(cache_directory, filename)
        sidecar = destination.with_suffix(destination.suffix + ".json")
        if _cached_png_is_valid(destination, sidecar, width, height, url):
            return destination, True
        destination.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        self.capabilities()
        maximum_bytes = width * height * 4 + 1_048_576
        try:
            download_wms_response(
                url,
                cache_directory,
                filename,
                content_type="image/png",
                maximum_bytes=maximum_bytes,
                timeout_seconds=self.timeout_seconds,
                retries=self.retries,
                validator=lambda path: validate_png(path, width, height),
                progress_callback=progress_callback,
                cancellation_requested=cancellation_requested,
                opener=self.opener,
                sleeper=self.sleeper,
            )
            _write_json_sidecar(
                sidecar,
                {
                    "schema_version": 1,
                    "provider_id": PROVIDER_ID,
                    "product_id": self.product.id,
                    "endpoint": self.product.endpoint,
                    "layer": contract.layer,
                    "format": contract.format,
                    "crs_epsg": contract.crs_epsg,
                    "bbox": [bounds.west, bounds.south, bounds.east, bounds.north],
                    "width": width,
                    "height": height,
                    "request_url_sha256": hashlib.sha256(url.encode()).hexdigest(),
                    "response_sha256": _file_sha256(destination),
                    "retrieved_at_utc": datetime.now(UTC).isoformat(),
                },
            )
            return destination, False
        except BaseException:
            if not sidecar.exists():
                destination.unlink(missing_ok=True)
            raise

    def _map_url(self, window: GeopfElevationRequest) -> str:
        return self._map_url_values(window.bounds, window.width, window.height)

    def _map_url_values(self, bounds: ProjectedBounds, width: int, height: int) -> str:
        contract = self.product.wms
        assert contract is not None
        query = urlencode(
            {
                "SERVICE": "WMS",
                "VERSION": contract.version,
                "REQUEST": "GetMap",
                "LAYERS": contract.layer,
                "STYLES": contract.style,
                "CRS": f"EPSG:{contract.crs_epsg}",
                "BBOX": f"{bounds.west},{bounds.south},{bounds.east},{bounds.north}",
                "WIDTH": str(width),
                "HEIGHT": str(height),
                "FORMAT": contract.format,
                "TRANSPARENT": "FALSE",
            }
        )
        return f"{self.product.endpoint}?{query}"

    def _read_bounded(self, request: Request, maximum_bytes: int) -> tuple[bytes, str]:
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                body = response.read(maximum_bytes + 1)
                content_type = response.headers.get_content_type().lower()
        except HTTPError as exc:
            raise ProviderUnavailableError(
                f"French WMS capabilities returned HTTP {exc.code}"
            ) from None
        except (URLError, TimeoutError, ConnectionError, OSError):
            raise ProviderUnavailableError("French WMS capabilities request failed") from None
        if len(body) > maximum_bytes:
            raise ProviderContractChanged("French WMS capabilities exceeds its size limit")
        return body, content_type


class GeopfWMSAcquirer:
    """Acquire confirmed French elevation or orthophoto WMS windows."""

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
        product = self.catalog.product(selection.product_id)
        if (
            selection.provider_id != PROVIDER_ID
            or product.provider_id != PROVIDER_ID
            or request.kind is not selection.kind
            or product.capabilities.kind is not selection.kind
            or product.wms is None
        ):
            raise ValueError("French WMS acquirer received an incompatible selection")
        resolution = request.target_resolution_m or product.capabilities.native_resolution_m
        if resolution < product.capabilities.native_resolution_m:
            raise ValueError("Requested resolution is finer than the French product")
        if selection.kind is DatasetKind.IMAGERY:
            return self._acquire_imagery(
                product,
                selection,
                resolution,
                roi,
                cache_directory,
                progress_callback,
                cancellation_requested,
            )
        if (
            selection.kind not in {DatasetKind.DTM, DatasetKind.DSM}
            or product.wms.sample_dtype != "<f4"
            or product.wms.nodata is None
        ):
            raise ValueError("French elevation acquirer received an incompatible selection")
        work_area = work_area_for_crs(roi, product.wms.crs_epsg)
        grid = align_projected_grid(project_work_area_bounds(work_area), resolution)
        windows = plan_geopf_elevation_requests(grid, product.wms.maximum_dimension)
        target = cache_directory / PROVIDER_ID / product.id
        client = GeopfWMSClient(product)
        paths: list[Path] = []
        cached_count = 0
        auxiliary: list[Path] = []
        for index, window in enumerate(windows):
            if cancellation_requested():
                raise JobCancelled("French elevation acquisition was cancelled")

            def report(written: int, expected: int | None, *, _index: int = index) -> None:
                if progress_callback is not None:
                    progress_callback(
                        TransferProgress(
                            selection.kind.value,
                            _index,
                            len(windows),
                            f"WMS block {_index + 1}/{len(windows)}",
                            written,
                            expected,
                        )
                    )

            path, cached = client.download_bil(
                window, target, report, cancellation_requested
            )
            if cached and progress_callback is not None:
                size = path.stat().st_size
                progress_callback(
                    TransferProgress(
                        selection.kind.value,
                        index,
                        len(windows),
                        f"WMS block {index + 1}/{len(windows)}",
                        size,
                        size,
                        cached=True,
                    )
                )
            paths.append(path)
            auxiliary.append(path.with_suffix(path.suffix + ".json"))
            cached_count += int(cached)
        verify_geopf_request_overlaps(tuple(paths), windows)
        return AcquiredRasterLayer(
            PROVIDER_ID,
            product.id,
            selection.kind,
            tuple(paths),
            cached_count,
            tuple(auxiliary),
        )

    @staticmethod
    def _acquire_imagery(
        product: ProductRecord,
        selection: ProductSelection,
        resolution: float,
        roi: BBoxWGS84,
        cache_directory: Path,
        progress_callback: Callable[[TransferProgress], None] | None,
        cancellation_requested: Callable[[], bool],
    ) -> AcquiredRasterLayer:
        contract = product.wms
        assert contract is not None
        if contract.format != "image/png":
            raise ValueError("French imagery requires the configured PNG contract")
        work_area = work_area_for_crs(roi, contract.crs_epsg)
        grid = align_projected_grid(project_work_area_bounds(work_area), resolution)
        tiles = tile_grid(grid, contract.maximum_dimension)
        target = cache_directory / PROVIDER_ID / product.id
        client = GeopfWMSClient(product)
        paths: list[Path] = []
        auxiliary: list[Path] = []
        cached_count = 0
        for index, tile in enumerate(tiles):
            if cancellation_requested():
                raise JobCancelled("French imagery acquisition was cancelled")

            def report(written: int, expected: int | None, *, _index: int = index) -> None:
                if progress_callback is not None:
                    progress_callback(
                        TransferProgress(
                            DatasetKind.IMAGERY.value,
                            _index,
                            len(tiles),
                            f"WMS block {_index + 1}/{len(tiles)}",
                            written,
                            expected,
                        )
                    )

            path, cached = client.download_png(
                tile.bounds,
                tile.columns,
                tile.rows,
                target,
                report,
                cancellation_requested,
            )
            if cached and progress_callback is not None:
                size = path.stat().st_size
                progress_callback(
                    TransferProgress(
                        DatasetKind.IMAGERY.value,
                        index,
                        len(tiles),
                        f"WMS block {index + 1}/{len(tiles)}",
                        size,
                        size,
                        cached=True,
                    )
                )
            paths.append(path)
            auxiliary.append(path.with_suffix(path.suffix + ".json"))
            cached_count += int(cached)
        return AcquiredRasterLayer(
            PROVIDER_ID,
            product.id,
            selection.kind,
            tuple(paths),
            cached_count,
            tuple(auxiliary),
        )


def verify_geopf_request_overlaps(
    paths: tuple[Path, ...], requests: tuple[GeopfElevationRequest, ...]
) -> None:
    """Require bit-identical shared node rows and columns between WMS blocks."""

    if len(paths) != len(requests):
        raise ValueError("WMS paths and requests must have matching lengths")
    arrays = tuple(
        np.memmap(path, dtype="<f4", mode="r", shape=(request.height, request.width))
        for path, request in zip(paths, requests, strict=True)
    )
    for first_index, first in enumerate(requests):
        for second_index in range(first_index + 1, len(requests)):
            second = requests[second_index]
            if (
                first.row == second.row
                and first.column + first.width - 1 == second.column
                and not np.array_equal(
                    arrays[first_index][:, -1], arrays[second_index][:, 0]
                )
            ):
                raise ProviderContractChanged("Adjacent French WMS columns do not match")
            if (
                first.column == second.column
                and first.row + first.height - 1 == second.row
                and not np.array_equal(
                    arrays[first_index][-1, :], arrays[second_index][0, :]
                )
            ):
                raise ProviderContractChanged("Adjacent French WMS rows do not match")


def _cached_png_is_valid(
    path: Path, sidecar: Path, width: int, height: int, request_url: str
) -> bool:
    if not path.is_file() or not sidecar.is_file():
        return False
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        validate_png(path, width, height)
        return isinstance(payload, dict) and bool(
            payload["schema_version"] == 1
            and payload["width"] == width
            and payload["height"] == height
            and payload["request_url_sha256"]
            == hashlib.sha256(request_url.encode()).hexdigest()
            and payload["response_sha256"] == _file_sha256(path)
        )
    except (OSError, KeyError, ValueError, json.JSONDecodeError, DownloadIntegrityError):
        return False


def _write_json_sidecar(path: Path, payload: dict[str, object]) -> None:
    part = path.with_name(path.name + ".part")
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    try:
        with part.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        finalize_part(part, path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
