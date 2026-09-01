"""Bounded acquisition of ESA WorldCover Sentinel-2 RGBNIR composites."""

from __future__ import annotations

import hashlib
import json
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
from ..io.atomic import finalize_part
from ..io.bigtiff_tiles import (
    BigTiffFloatTileReader,
    GeoReference,
    PixelWindow,
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
        cached_paths = _cached_windows(target)
        if cached_paths:
            for index, path in enumerate(cached_paths, start=1):
                _report(progress_callback, index, len(cached_paths), path, True)
            return AcquiredRasterLayer(
                PROVIDER_ID,
                PRODUCT_ID,
                DatasetKind.IMAGERY,
                cached_paths,
                len(cached_paths),
            )
        windows: list[tuple[WindowReader, ProjectedBounds, Path]] = []
        for name, bounds, url in tiles:
            if cancellation_requested():
                raise JobCancelled("WorldCover acquisition was cancelled")
            try:
                reader = self._reader_factory(url, target / "ranges" / name)
            except NoCoverageError:
                continue
            source_window = reader.georeference.enclosing_window(bounds)
            for part, window in enumerate(_split_window(source_window)):
                windows.append(
                    (
                        reader,
                        reader.georeference.window_bounds(window),
                        target / f"rgbnir_{name}_{part:03d}.npy",
                    )
                )
        if not windows:
            raise NoCoverageError("WorldCover has no imagery for this ROI")
        paths: list[Path] = []
        cached_count = 0
        for index, (reader, bounds, path) in enumerate(windows, start=1):
            if cancellation_requested():
                raise JobCancelled("WorldCover acquisition was cancelled")
            if imagery_window_is_valid(path):
                paths.append(path)
                cached_count += 1
                _report(progress_callback, index, len(windows), path, True)
                continue
            path.unlink(missing_ok=True)
            path.with_suffix(path.suffix + ".json").unlink(missing_ok=True)
            data, exact_bounds = reader.read_bounds(bounds)
            if data.ndim != 3 or data.shape[2] != 4:
                raise RasterFormatError("WorldCover RGBNIR source must contain four bands")
            if data.shape[0] * data.shape[1] > MAXIMUM_WINDOW_PIXELS:
                raise RasterFormatError("WorldCover reader exceeded the window pixel limit")
            write_imagery_window(path, data, exact_bounds, reader.nodata, BANDS)
            paths.append(path)
            _report(progress_callback, index, len(windows), path, False)
        _write_window_index(target, tuple(paths))
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


def _split_window(window: PixelWindow) -> tuple[PixelWindow, ...]:
    """Split one source window without exceeding the in-memory pixel budget."""

    side = math.isqrt(MAXIMUM_WINDOW_PIXELS)
    return tuple(
        PixelWindow(
            row,
            column,
            min(side, window.row + window.height - row),
            min(side, window.column + window.width - column),
        )
        for row in range(window.row, window.row + window.height, side)
        for column in range(window.column, window.column + window.width, side)
    )


def _cached_windows(directory: Path) -> tuple[Path, ...]:
    index = directory / "windows.json"
    try:
        names = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if (
        not isinstance(names, list)
        or not names
        or not all(
            isinstance(name, str)
            and Path(name).name == name
            and name.endswith(".npy")
            for name in names
        )
    ):
        return ()
    paths = tuple(directory / name for name in names)
    return paths if all(imagery_window_is_valid(path) for path in paths) else ()


def _write_window_index(directory: Path, paths: tuple[Path, ...]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    index = directory / "windows.json"
    part = index.with_suffix(".json.part")
    part.unlink(missing_ok=True)
    part.write_text(
        json.dumps([path.name for path in paths], separators=(",", ":")),
        encoding="utf-8",
    )
    finalize_part(part, index)


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
