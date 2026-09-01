"""Bounded acquisition of GEDTM30 elevation and uncertainty windows."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from ..catalog.models import DatasetKind
from ..catalog.selection import LayerRequest, ProductSelection
from ..core.acquisition import AcquiredRasterLayer
from ..core.delivery import TransferProgress
from ..core.roi import BBoxWGS84
from ..errors import JobCancelled, RasterFormatError
from ..io.atomic import finalize_part
from ..io.bigtiff_tiles import (
    BigTiffFloatTileReader,
    GeoReference,
    PixelWindow,
    TileLayout,
    open_float_tile_reader,
)
from ..io.elevation_window import elevation_window_is_valid, write_elevation_window
from ..io.random_access import HttpRangeReader
from ..models import ProjectedBounds

GEDTM30_PRODUCT_ID = "GEDTM30_V11"
GEDTM30_PROVIDER_ID = "openlandmap"
GEDTM30_BASE_URL = "https://s3.opengeohub.org/global/edtm"
GEDTM30_ELEVATION_URL = (
    f"{GEDTM30_BASE_URL}/"
    "gedtm_rf_m_30m_s_20060101_20151231_go_epsg.4326.3855_v20250611.tif"
)
GEDTM30_UNCERTAINTY_URL = (
    f"{GEDTM30_BASE_URL}/"
    "gedtm_rf_std_30m_s_20060101_20151231_go_epsg.4326.3855_v20250611.tif"
)
JRC_GSW_BASE_URL = (
    "https://s3.waw4-1.cloudferro.com/swift/v1/global-surface-water/"
    "download2024/Aggregated/VER1-5/occurrence"
)
JRC_GSW_ATTRIBUTION = "Source: EC JRC/Google"
JRC_GSW_PERMANENT_WATER_THRESHOLD = 90
JRC_GSW_NODATA = 255.0
MAXIMUM_SOURCE_BYTES = 500_000_000_000
MAXIMUM_WINDOW_PIXELS = 16_777_216


class WindowReader(Protocol):
    layout: TileLayout
    georeference: GeoReference

    @property
    def nodata(self) -> float: ...

    def window_for_bounds(self, bounds: ProjectedBounds) -> PixelWindow: ...

    def read_bounds(
        self, bounds: ProjectedBounds
    ) -> tuple[NDArray[np.float32], ProjectedBounds]: ...

    def read_window(
        self, row: int, column: int, height: int, width: int
    ) -> NDArray[np.float32]: ...


ReaderFactory = Callable[[str, Path], WindowReader]


class Gedtm30Acquirer:
    """Cache ROI windows from the global DTM and its uncertainty asset."""

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
            selection.provider_id != GEDTM30_PROVIDER_ID
            or selection.product_id != GEDTM30_PRODUCT_ID
            or selection.kind is not DatasetKind.DTM
            or request.kind is not DatasetKind.DTM
        ):
            raise ValueError("GEDTM30 acquirer received an incompatible selection")
        key = hashlib.sha256(
            f"window-v3|{roi.west},{roi.south},{roi.east},{roi.north}".encode("ascii")
        ).hexdigest()[:20]
        target = cache_directory / selection.provider_id / selection.product_id / key
        elevation_path = target / "elevation.npy"
        uncertainty_path = target / "uncertainty.npy"
        statistics_path = target / "uncertainty.json"
        water_tiles = _jrc_water_tiles(roi)
        water_paths = tuple(target / f"water_occurrence_{name}.npy" for name, _, _ in water_tiles)
        water_metadata_path = target / "water_mask.json"
        if (
            elevation_window_is_valid(elevation_path)
            and elevation_window_is_valid(uncertainty_path)
            and _statistics_are_valid(statistics_path)
            and all(elevation_window_is_valid(path) for path in water_paths)
            and _water_metadata_is_valid(water_metadata_path)
        ):
            total = 2 + len(water_paths)
            _report(progress_callback, 1, total, elevation_path, cached=True)
            _report(progress_callback, 2, total, uncertainty_path, cached=True)
            for index, path in enumerate(water_paths, start=3):
                _report(progress_callback, index, total, path, cached=True)
            return AcquiredRasterLayer(
                selection.provider_id,
                selection.product_id,
                selection.kind,
                (elevation_path,),
                1,
                (uncertainty_path, statistics_path, *water_paths, water_metadata_path),
            )
        if cancellation_requested():
            raise JobCancelled("GEDTM30 acquisition was cancelled")
        bounds = ProjectedBounds(roi.west, roi.south, roi.east, roi.north, 4326)
        elevation_reader = self._reader_factory(
            GEDTM30_ELEVATION_URL, target / "ranges" / "elevation"
        )
        elevation, elevation_bounds = _read_source_window(elevation_reader, bounds)
        total = 2 + len(water_paths)
        _report(progress_callback, 1, total, elevation_path)
        if cancellation_requested():
            raise JobCancelled("GEDTM30 acquisition was cancelled")
        uncertainty_reader = self._reader_factory(
            GEDTM30_UNCERTAINTY_URL, target / "ranges" / "uncertainty"
        )
        uncertainty, uncertainty_bounds = _read_source_window(uncertainty_reader, bounds)
        if elevation.shape != uncertainty.shape or elevation_bounds != uncertainty_bounds:
            raise RasterFormatError("GEDTM30 elevation and uncertainty grids do not align")
        target.mkdir(parents=True, exist_ok=True)
        _discard_incomplete_artifacts(
            elevation_path,
            uncertainty_path,
            statistics_path,
            *water_paths,
            water_metadata_path,
        )
        write_elevation_window(
            elevation_path, elevation, elevation_bounds, elevation_reader.nodata
        )
        write_elevation_window(
            uncertainty_path,
            uncertainty,
            uncertainty_bounds,
            uncertainty_reader.nodata,
        )
        _write_statistics(statistics_path, uncertainty, uncertainty_reader.nodata)
        _report(progress_callback, 2, total, uncertainty_path)
        for index, ((_, water_bounds, url), path) in enumerate(
            zip(water_tiles, water_paths, strict=True), start=3
        ):
            if cancellation_requested():
                raise JobCancelled("GEDTM30 water mask acquisition was cancelled")
            reader = self._reader_factory(url, target / "ranges" / path.stem)
            occurrence, occurrence_bounds = _read_source_window(reader, water_bounds)
            write_elevation_window(path, occurrence, occurrence_bounds, JRC_GSW_NODATA)
            _report(progress_callback, index, total, path)
        _write_json(
            water_metadata_path,
            {
                "product": "JRC Global Surface Water v1.5 occurrence 1984-2024",
                "attribution": JRC_GSW_ATTRIBUTION,
                "permanent_water_threshold_percent": JRC_GSW_PERMANENT_WATER_THRESHOLD,
                "permanent_water_expression": "90 <= occurrence <= 100",
                "nodata_value": JRC_GSW_NODATA,
                "policy": "QA_ONLY_DO_NOT_FLATTEN_ELEVATION",
                "coverage": _water_coverage(roi),
            },
        )
        return AcquiredRasterLayer(
            selection.provider_id,
            selection.product_id,
            selection.kind,
            (elevation_path,),
            0,
            (uncertainty_path, statistics_path, *water_paths, water_metadata_path),
        )


def _remote_reader(url: str, cache_directory: Path) -> BigTiffFloatTileReader:
    source = HttpRangeReader(
        url,
        cache_directory,
        allowed_hosts=frozenset(
            {"s3.opengeohub.org", "s3.waw4-1.cloudferro.com"}
        ),
        maximum_source_bytes=MAXIMUM_SOURCE_BYTES,
    )
    return open_float_tile_reader(source)


def _read_source_window(
    reader: WindowReader, bounds: ProjectedBounds
) -> tuple[NDArray[np.float32], ProjectedBounds]:
    window = reader.window_for_bounds(bounds)
    padding = 4
    row = max(0, window.row - padding)
    column = max(0, window.column - padding)
    row_end = min(reader.layout.height, window.row + window.height + padding)
    column_end = min(reader.layout.width, window.column + window.width + padding)
    expanded = PixelWindow(row, column, row_end - row, column_end - column)
    if expanded.width * expanded.height > MAXIMUM_WINDOW_PIXELS:
        raise RasterFormatError("GEDTM30 source window exceeds the configured pixel limit")
    return (
        reader.read_window(
            expanded.row, expanded.column, expanded.height, expanded.width
        ),
        reader.georeference.window_bounds(expanded),
    )


def _discard_incomplete_artifacts(*paths: Path) -> None:
    for path in paths:
        path.unlink(missing_ok=True)
        if path.suffix == ".npy":
            path.with_suffix(path.suffix + ".json").unlink(missing_ok=True)


def _write_statistics(path: Path, data: NDArray[np.float32], nodata: float) -> None:
    valid = data[data != nodata]
    if not valid.size:
        raise RasterFormatError("GEDTM30 uncertainty window contains no valid samples")
    payload = {
        "minimum_m": float(valid.min()),
        "mean_m": float(valid.mean()),
        "p95_m": float(np.percentile(valid, 95)),
        "maximum_m": float(valid.max()),
        "valid_samples": int(valid.size),
    }
    _write_json(path, payload)


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    part = path.with_name(path.name + ".part")
    try:
        with part.open("xb") as stream:
            stream.write(json.dumps(payload, sort_keys=True).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        finalize_part(part, path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def _jrc_water_tiles(
    roi: BBoxWGS84,
) -> tuple[tuple[str, ProjectedBounds, str], ...]:
    south = max(roi.south, -60.0)
    north = min(roi.north, 80.0)
    if north <= south:
        return ()
    first_west = int(np.floor(roi.west / 10.0)) * 10
    last_west = int(np.floor((roi.east - 1e-12) / 10.0)) * 10
    first_north = int(np.ceil(north / 10.0)) * 10
    tiles: list[tuple[str, ProjectedBounds, str]] = []
    for west in range(first_west, last_west + 1, 10):
        for tile_north in range(first_north, int(np.floor(south / 10.0)) * 10, -10):
            tile_south = tile_north - 10
            bounds = ProjectedBounds(
                max(roi.west, west),
                max(south, tile_south),
                min(roi.east, west + 10),
                min(north, tile_north),
                4326,
            )
            longitude = f"{abs(west)}{'W' if west < 0 else 'E'}"
            latitude = f"{abs(tile_north)}{'S' if tile_north < 0 else 'N'}"
            name = f"{longitude}_{latitude}"
            url = f"{JRC_GSW_BASE_URL}/occurrence_{name}_v1_5_2024.tif"
            tiles.append((name, bounds, url))
    return tuple(tiles)


def _water_coverage(roi: BBoxWGS84) -> str:
    return "full" if roi.south >= -60.0 and roi.north <= 80.0 else "partial"


def _statistics_are_valid(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return all(
            isinstance(payload[key], (int, float))
            for key in ("minimum_m", "mean_m", "p95_m", "maximum_m", "valid_samples")
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return False


def _water_metadata_is_valid(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return bool(
            payload["permanent_water_expression"] == "90 <= occurrence <= 100"
            and payload["nodata_value"] == JRC_GSW_NODATA
            and payload["policy"] == "QA_ONLY_DO_NOT_FLATTEN_ELEVATION"
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return False


def _report(
    callback: Callable[[TransferProgress], None] | None,
    completed: int,
    total: int,
    path: Path,
    *,
    cached: bool = False,
) -> None:
    if callback is None:
        return
    size = path.stat().st_size if path.is_file() else 0
    callback(
        TransferProgress(
            DatasetKind.DTM.value,
            completed - 1,
            total,
            path.name,
            size,
            size if cached else None,
            cached,
        )
    )
