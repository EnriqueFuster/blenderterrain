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
from ...io.bigtiff_tiles import BigTiffFloatTileReader, open_float_tile_reader
from ...io.imagery_window import imagery_window_is_valid, write_imagery_window
from ...io.random_access import HttpRangeReader
from ...models import ProjectedBounds
from .swisstopo import (
    ASSET_HOST,
    SWISSIMAGE_COLLECTION,
    SwisstopoAsset,
    SwisstopoCatalogClient,
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
_MAXIMUM_WINDOW_PIXELS = 67_108_864


class _ImageryReader(Protocol):
    georeference: Any
    layout: Any

    @property
    def nodata(self) -> float: ...

    def read_bounds(
        self, bounds: ProjectedBounds
    ) -> tuple[NDArray[np.float32], ProjectedBounds]: ...


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
            f"imagery-v1|{roi}|{request.target_resolution_m}".encode()
        ).hexdigest()[:20]
        target = cache_directory / selection.provider_id / product.id / key
        projected_roi = project_swiss_roi(roi)
        paths: list[Path] = []
        cached_count = 0
        used = []
        for index, item in enumerate(items):
            if cancellation_requested():
                raise JobCancelled("SWISSIMAGE acquisition was cancelled")
            asset = select_swisstopo_asset(item, request.target_resolution_m)
            identity = f"{item.id}|{asset.sha256}"
            path = target / f"{hashlib.sha256(identity.encode()).hexdigest()[:20]}.npy"
            cached = imagery_window_is_valid(path)
            if not cached:
                reader = self._reader_factory(asset, target / "ranges" / path.stem)
                _validate_imagery_reader(reader, asset)
                overlap = intersect_projected_bounds(
                    projected_roi, raster_reader_bounds(reader)
                )
                if overlap is None:
                    continue
                window = reader.georeference.enclosing_window(overlap)
                if window.width * window.height > _MAXIMUM_WINDOW_PIXELS:
                    raise RasterFormatError(
                        "SWISSIMAGE source window exceeds the pixel limit; "
                        "use a coarser imagery resolution or smaller ROI"
                    )
                data, exact = reader.read_bounds(overlap)
                if data.dtype != np.float32 or data.ndim != 3 or data.shape[2] != 3:
                    raise RasterFormatError("SWISSIMAGE window must contain three RGB bands")
                path.unlink(missing_ok=True)
                path.with_suffix(".npy.json").unlink(missing_ok=True)
                write_imagery_window(
                    path, data, exact, reader.nodata, ("B04", "B03", "B02")
                )
            else:
                cached_count += 1
            paths.append(path)
            used.append((item, asset))
            if progress_callback is not None:
                size = path.stat().st_size
                progress_callback(
                    TransferProgress(
                        DatasetKind.IMAGERY.value,
                        index,
                        len(items),
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
