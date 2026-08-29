"""Portable local artifacts for bounded, georeferenced elevation windows."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from ..errors import RasterFormatError
from ..models import ProjectedBounds
from .atomic import finalize_part
from .bigtiff_tiles import GeoReference, TileLayout

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ElevationWindowMetadata:
    epsg: int
    origin_x: float
    origin_y: float
    pixel_width: float
    pixel_height: float
    nodata: float


class ElevationWindowReader:
    """Read one bounded Float32 array with an adjacent metadata manifest."""

    def __init__(self, path: Path) -> None:
        metadata = _read_metadata(_metadata_path(path))
        data = np.load(path, mmap_mode="r", allow_pickle=False)
        if data.dtype != np.float32 or data.ndim != 2 or min(data.shape) < 2:
            raise RasterFormatError("Elevation window must be a two-dimensional Float32 array")
        self._data = data
        self.layout = TileLayout(
            data.shape[1], data.shape[0], data.shape[1], data.shape[0], metadata.nodata
        )
        self.georeference = GeoReference(
            metadata.epsg,
            metadata.origin_x,
            metadata.origin_y,
            metadata.pixel_width,
            metadata.pixel_height,
            metadata.epsg,
        )

    @property
    def nodata(self) -> float:
        nodata = self.layout.nodata
        if nodata is None:
            raise RasterFormatError("Elevation window has no NoData value")
        return nodata

    def read_bounds(
        self, bounds: ProjectedBounds
    ) -> tuple[NDArray[np.float32], ProjectedBounds]:
        window = self.georeference.enclosing_window(bounds)
        if (
            window.row + window.height > self.layout.height
            or window.column + window.width > self.layout.width
        ):
            raise RasterFormatError("Requested bounds extend outside the elevation window")
        data = np.asarray(
            self._data[
                window.row : window.row + window.height,
                window.column : window.column + window.width,
            ],
            dtype=np.float32,
        )
        return data, self.georeference.window_bounds(window)


def write_elevation_window(
    path: Path,
    data: NDArray[np.float32],
    bounds: ProjectedBounds,
    nodata: float,
) -> None:
    """Atomically publish one array and its exact outer pixel bounds."""

    if path.suffix.lower() != ".npy" or data.dtype != np.float32 or data.ndim != 2:
        raise RasterFormatError("Elevation window output must be a Float32 .npy array")
    if min(data.shape) < 2:
        raise RasterFormatError("Elevation window requires at least two rows and columns")
    pixel_width = (bounds.east - bounds.west) / data.shape[1]
    pixel_height = -(bounds.north - bounds.south) / data.shape[0]
    metadata = ElevationWindowMetadata(
        bounds.epsg,
        bounds.west,
        bounds.north,
        pixel_width,
        pixel_height,
        nodata,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or _metadata_path(path).exists():
        raise RasterFormatError("Refusing to overwrite an elevation window artifact")
    array_part = path.with_name(path.name + ".part")
    metadata_path = _metadata_path(path)
    metadata_part = metadata_path.with_name(metadata_path.name + ".part")
    try:
        with array_part.open("xb") as stream:
            np.save(stream, data, allow_pickle=False)
            stream.flush()
            os.fsync(stream.fileno())
        encoded = json.dumps(
            {"schema_version": SCHEMA_VERSION, **asdict(metadata)},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        with metadata_part.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        finalize_part(array_part, path)
        finalize_part(metadata_part, metadata_path)
    except BaseException:
        array_part.unlink(missing_ok=True)
        metadata_part.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        raise


def elevation_window_is_valid(path: Path) -> bool:
    try:
        ElevationWindowReader(path)
    except (OSError, ValueError, RasterFormatError):
        return False
    return True


def _metadata_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".json")


def _read_metadata(path: Path) -> ElevationWindowMetadata:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.pop("schema_version") != SCHEMA_VERSION:
            raise ValueError
        return ElevationWindowMetadata(**payload)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise RasterFormatError("Elevation window metadata is invalid") from exc
