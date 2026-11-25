"""SWISSIMAGE COG acquisition and texture preparation."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from ...catalog import (
    Catalog,
    CoverageMatch,
    DatasetKind,
    LayerRequest,
    ProductSelection,
    load_bundled_catalog,
)
from ...core.acquisition import AcquiredRasterLayer
from ...core.delivery import TransferProgress
from ...core.imagery_processing import ProcessedImageryTile, process_imagery_windows
from ...core.planning import ImportPlan
from ...core.roi import BBoxWGS84
from ...errors import JobCancelled, NoCoverageError, RasterFormatError
from ...io.bigtiff_tiles import (
    BigTiffFloatTileReader,
    PixelWindow,
    open_float_tile_reader,
    split_pixel_window,
)
from ...io.imagery_window import imagery_window_is_valid, write_imagery_window
from ...io.random_access import HttpRangeReader
from ...models import ProjectedBounds
from .swisstopo import (
    ASSET_HOST,
    SWISSIMAGE_COLLECTION,
    SwisstopoAsset,
    SwisstopoCatalogClient,
    SwisstopoItem,
    intersect_projected_bounds,
    project_swiss_roi,
    raster_reader_bounds,
    select_latest_swisstopo_items,
    select_swisstopo_asset,
    write_swisstopo_manifest,
)

PRODUCT_ID = "CH_SWISSIMAGE_DOP10"
_SUPPORTED_RESOLUTIONS = frozenset({0.1, 2.0})
_MAXIMUM_SOURCE_BYTES = 100_000_000
_MAXIMUM_WINDOW_PIXELS = 16_777_216


class _ImageryReader(Protocol):
    georeference: Any
    layout: Any

    @property
    def nodata(self) -> float: ...

    def read_window(
        self, row: int, column: int, height: int, width: int
    ) -> NDArray[np.float32]: ...


ImageryReaderFactory = Callable[[SwisstopoAsset, Path], _ImageryReader]


def process_swissimage_imagery(
    source_paths: tuple[Path, ...],
    plan: ImportPlan,
    output_directory: Path,
    progress_callback: Callable[[int, int], None] | None = None,
    cancellation_requested: Callable[[], bool] = lambda: False,
) -> tuple[ProcessedImageryTile, ...]:
    """Project prepared SWISSIMAGE RGB samples without reflectance stretching."""

    return process_imagery_windows(
        source_paths,
        plan,
        output_directory,
        "swissimage",
        progress_callback,
        cancellation_requested,
        sample_encoding="uint8",
    )


class SwisstopoImageryAcquirer:
    """Read bounded RGB windows from the newest intersecting SWISSIMAGE COGs."""

    def __init__(
        self,
        catalog: Catalog | None = None,
        stac: SwisstopoCatalogClient | None = None,
        reader_factory: ImageryReaderFactory | None = None,
    ) -> None:
        self._catalog = catalog or load_bundled_catalog()
        self._stac = stac or SwisstopoCatalogClient()
        self._reader_factory = reader_factory or _remote_imagery_reader

    def acquire(
        self,
        selection: ProductSelection,
        request: LayerRequest,
        roi: BBoxWGS84,
        cache_directory: Path,
        progress_callback: Callable[[TransferProgress], None] | None = None,
        cancellation_requested: Callable[[], bool] = lambda: False,
    ) -> AcquiredRasterLayer:
        product = self._catalog.product(selection.product_id)
        if (
            product.id != PRODUCT_ID
            or selection.provider_id != "swisstopo"
            or product.provider_id != selection.provider_id
            or selection.kind is not DatasetKind.IMAGERY
            or request.kind is not DatasetKind.IMAGERY
            or product.capabilities.kind is not DatasetKind.IMAGERY
        ):
            raise ValueError("swisstopo imagery requires a compatible selection")
        if product.coverage.match(roi) is CoverageMatch.NONE:
            raise NoCoverageError("SWISSIMAGE does not intersect the requested ROI")
        items = select_latest_swisstopo_items(
            self._stac.search(SWISSIMAGE_COLLECTION, roi, _SUPPORTED_RESOLUTIONS)
        )
        if not items:
            raise NoCoverageError("SWISSIMAGE has no STAC items for the ROI")

        key = hashlib.sha256(
            f"imagery-v3|{roi}|{request.target_resolution_m}".encode()
        ).hexdigest()[:20]
        target = cache_directory / selection.provider_id / product.id / key
        projected_roi = project_swiss_roi(roi)
        tasks: list[tuple[_ImageryReader, PixelWindow, Path, int]] = []
        used: list[tuple[SwisstopoItem, SwisstopoAsset]] = []
        for item in items:
            if cancellation_requested():
                raise JobCancelled("SWISSIMAGE acquisition was cancelled")
            asset = select_swisstopo_asset(item, request.target_resolution_m)
            identity = f"{item.id}|{asset.sha256}"
            stem = hashlib.sha256(identity.encode()).hexdigest()[:20]
            reader = self._reader_factory(asset, target / "ranges" / stem)
            _validate_imagery_reader(reader, asset)
            overlap = intersect_projected_bounds(
                projected_roi, raster_reader_bounds(reader)
            )
            if overlap is None:
                continue
            reduction = _reduction_factor(reader, asset, request.target_resolution_m)
            source_window = _aligned_source_window(reader, overlap, reduction)
            for part, window in enumerate(
                split_pixel_window(
                    source_window, _MAXIMUM_WINDOW_PIXELS, alignment=reduction
                )
            ):
                tasks.append(
                    (
                        reader,
                        window,
                        target / f"{stem}_{reduction}_{part:03d}.npy",
                        reduction,
                    )
                )
            used.append((item, asset))

        paths: list[Path] = []
        cached_count = 0
        for index, (reader, window, path, reduction) in enumerate(tasks, start=1):
            if cancellation_requested():
                raise JobCancelled("SWISSIMAGE acquisition was cancelled")
            cached = imagery_window_is_valid(path)
            if not cached:
                data = reader.read_window(
                    window.row, window.column, window.height, window.width
                )
                exact = reader.georeference.window_bounds(window)
                if data.dtype != np.float32 or data.ndim != 3 or data.shape[2] != 3:
                    raise RasterFormatError("SWISSIMAGE window must contain three RGB bands")
                data = _reduce_rgb(data, reader.nodata, reduction)
                path.unlink(missing_ok=True)
                path.with_suffix(".npy.json").unlink(missing_ok=True)
                write_imagery_window(
                    path, data, exact, reader.nodata, ("B04", "B03", "B02")
                )
            else:
                cached_count += 1
            paths.append(path)
            if progress_callback is not None:
                size = path.stat().st_size
                progress_callback(
                    TransferProgress(
                        DatasetKind.IMAGERY.value,
                        index,
                        len(tasks),
                        path.name,
                        size,
                        size,
                        cached=cached,
                    )
                )
        if not paths:
            raise NoCoverageError("SWISSIMAGE has no raster pixels for the ROI")
        manifest = target / "source_manifest.json"
        write_swisstopo_manifest(
            manifest, product.id, used, vertical_reference=None
        )
        return AcquiredRasterLayer(
            selection.provider_id,
            product.id,
            DatasetKind.IMAGERY,
            tuple(paths),
            cached_count,
            (manifest,),
        )


def _remote_imagery_reader(
    asset: SwisstopoAsset, cache_directory: Path
) -> BigTiffFloatTileReader:
    return open_float_tile_reader(
        HttpRangeReader(
            asset.url,
            cache_directory,
            allowed_hosts=frozenset({ASSET_HOST}),
            maximum_source_bytes=_MAXIMUM_SOURCE_BYTES,
            cache_key=asset.sha256,
        )
    )


def _validate_imagery_reader(reader: _ImageryReader, asset: SwisstopoAsset) -> None:
    reference = reader.georeference
    if (
        reference.epsg != 2056
        or abs(reference.pixel_width - asset.gsd) > 1e-6
        or abs(reference.pixel_height + asset.gsd) > 1e-6
    ):
        raise RasterFormatError("SWISSIMAGE asset grid changed")


def _reduction_factor(
    reader: _ImageryReader,
    asset: SwisstopoAsset,
    target_resolution_m: float | None,
) -> int:
    """Return an integer source reduction that never undersamples the target grid."""

    if target_resolution_m is None:
        return 1
    factor = max(1, int(target_resolution_m / asset.gsd + 1e-9))
    if reader.layout.width % factor or reader.layout.height % factor:
        return 1
    return factor


def _aligned_source_window(
    reader: _ImageryReader, bounds: ProjectedBounds, alignment: int
) -> PixelWindow:
    window = reader.georeference.enclosing_window(bounds)
    row = window.row - window.row % alignment
    column = window.column - window.column % alignment
    row_end = min(
        reader.layout.height,
        ((window.row + window.height + alignment - 1) // alignment) * alignment,
    )
    column_end = min(
        reader.layout.width,
        ((window.column + window.width + alignment - 1) // alignment) * alignment,
    )
    return PixelWindow(row, column, row_end - row, column_end - column)


def _reduce_rgb(
    data: NDArray[np.float32], nodata: float, factor: int
) -> NDArray[np.float32]:
    if factor == 1:
        return data
    height, width, bands = data.shape
    if height % factor or width % factor:
        raise RasterFormatError("SWISSIMAGE reduction window is not grid-aligned")
    blocks = data.reshape(height // factor, factor, width // factor, factor, bands)
    valid = np.any(data != nodata, axis=2).reshape(
        height // factor, factor, width // factor, factor
    )
    counts = valid.sum(axis=(1, 3))
    reduced = np.full((height // factor, width // factor, bands), nodata, np.float32)
    covered = counts > 0
    for band in range(bands):
        sums = np.asarray(
            np.sum(blocks[..., band], axis=(1, 3), where=valid, dtype=np.float32)
        )
        reduced[..., band][covered] = sums[covered] / counts[covered]
    return reduced
