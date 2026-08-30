"""Bounded acquisition of ESA WorldCover Sentinel-2 RGBNIR composites."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from ..catalog.models import DatasetKind
from ..catalog.selection import LayerRequest, ProductSelection
from ..core.acquisition import AcquiredRasterLayer
from ..core.delivery import TransferProgress
from ..core.roi import BBoxWGS84
from ..errors import JobCancelled, NoCoverageError, RasterFormatError
from ..io.bigtiff_tiles import (
    BigTiffFloatTileReader,
    GeoReference,
    TileLayout,
    open_float_tile_reader,
)
from ..io.imagery_window import imagery_window_is_valid, write_imagery_window
from ..io.random_access import HttpRangeReader
from ..models import ProjectedBounds

PRODUCT_ID = "ESA_WORLDCOVER_S2_2021"
PROVIDER_ID = "esa_worldcover"
BASE_URL = "https://esa-worldcover-s2.s3.eu-central-1.amazonaws.com/rgbnir/2021"
HOST = "esa-worldcover-s2.s3.eu-central-1.amazonaws.com"
BANDS = ("B02", "B03", "B04", "B08")
MAXIMUM_SOURCE_BYTES = 1_000_000_000
MAXIMUM_WINDOW_PIXELS = 16_777_216


class WindowReader(Protocol):
    layout: TileLayout
    georeference: GeoReference

    @property
    def nodata(self) -> float: ...

    def read_bounds(
        self, bounds: ProjectedBounds
    ) -> tuple[NDArray[np.float32], ProjectedBounds]: ...


ReaderFactory = Callable[[str, Path], WindowReader]


class WorldCoverAcquirer:
    """Cache bounded RGBNIR windows from intersecting one-degree COGs."""

    def __init__(self, reader_factory: ReaderFactory | None = None) -> None:
        self._reader_factory = reader_factory or _remote_reader

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
            or selection.product_id != PRODUCT_ID
            or selection.kind is not DatasetKind.IMAGERY
            or request.kind is not DatasetKind.IMAGERY
        ):
            raise ValueError("WorldCover acquirer received an incompatible selection")
        tiles = _tiles(roi)
        key = hashlib.sha256(
            f"window-v1|{roi.west},{roi.south},{roi.east},{roi.north}".encode("ascii")
        ).hexdigest()[:20]
        target = cache_directory / PROVIDER_ID / PRODUCT_ID / key
        expected_paths = tuple(target / f"rgbnir_{name}.npy" for name, _, _ in tiles)
        paths: list[Path] = []
        cached_count = 0
        for index, ((_, bounds, url), path) in enumerate(
            zip(tiles, expected_paths, strict=True), start=1
        ):
            if cancellation_requested():
                raise JobCancelled("WorldCover acquisition was cancelled")
            if imagery_window_is_valid(path):
                paths.append(path)
                cached_count += 1
                _report(progress_callback, index, len(tiles), path, True)
                continue
            path.unlink(missing_ok=True)
            path.with_suffix(path.suffix + ".json").unlink(missing_ok=True)
            try:
                reader = self._reader_factory(url, target / "ranges" / path.stem)
            except NoCoverageError:
                continue
            window = reader.georeference.enclosing_window(bounds)
            if window.width * window.height > MAXIMUM_WINDOW_PIXELS:
                raise RasterFormatError("WorldCover source window exceeds the pixel limit")
            data, exact_bounds = reader.read_bounds(bounds)
            if data.ndim != 3 or data.shape[2] != 4:
                raise RasterFormatError("WorldCover RGBNIR source must contain four bands")
            write_imagery_window(path, data, exact_bounds, reader.nodata, BANDS)
            paths.append(path)
            _report(progress_callback, index, len(tiles), path, False)
        if not paths:
            raise NoCoverageError("WorldCover has no imagery for this ROI")
        return AcquiredRasterLayer(
            PROVIDER_ID, PRODUCT_ID, DatasetKind.IMAGERY, tuple(paths), cached_count
        )


def _remote_reader(url: str, cache_directory: Path) -> BigTiffFloatTileReader:
    source = HttpRangeReader(
        url,
        cache_directory,
        allowed_hosts=frozenset({HOST}),
        maximum_source_bytes=MAXIMUM_SOURCE_BYTES,
    )
    return open_float_tile_reader(source)


def _tiles(roi: BBoxWGS84) -> tuple[tuple[str, ProjectedBounds, str], ...]:
    south = max(roi.south, -60.0)
    north = min(roi.north, 84.0)
    if north <= south:
        raise RasterFormatError("WorldCover does not cover this ROI")
    tiles: list[tuple[str, ProjectedBounds, str]] = []
    for longitude in range(math.floor(roi.west), math.ceil(roi.east)):
        for latitude in range(math.floor(south), math.ceil(north)):
            name = (
                f"{'N' if latitude >= 0 else 'S'}{abs(latitude):02d}"
                f"{'E' if longitude >= 0 else 'W'}{abs(longitude):03d}"
            )
            bounds = ProjectedBounds(
                max(roi.west, longitude),
                max(south, latitude),
                min(roi.east, longitude + 1),
                min(north, latitude + 1),
                4326,
            )
            url = f"{BASE_URL}/{name[:3]}/ESA_WorldCover_10m_2021_v200_{name}_S2RGBNIR.tif"
            tiles.append((name, bounds, url))
    return tuple(tiles)


def _report(
    callback: Callable[[TransferProgress], None] | None,
    completed: int,
    total: int,
    path: Path,
    cached: bool,
) -> None:
    if callback is not None:
        size = path.stat().st_size if path.is_file() else 0
        callback(
            TransferProgress(
                DatasetKind.IMAGERY.value,
                completed - 1,
                total,
                path.name,
                size,
                size if cached else None,
                cached,
            )
        )
