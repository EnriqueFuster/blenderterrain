"""Portable local artifacts for bounded georeferenced RGBNIR windows."""

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
from .bigtiff_tiles import GeoReference

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ImageryWindowMetadata:
    epsg: int
    origin_x: float
    origin_y: float
    pixel_width: float
    pixel_height: float
    nodata: float
    bands: tuple[str, ...]


class ImageryWindowReader:
    """Read one bounded Float32 RGB or RGBNIR array and its georeferencing."""

    def __init__(self, path: Path) -> None:
        metadata = _read_metadata(path.with_suffix(path.suffix + ".json"))
        data = np.load(path, mmap_mode="r", allow_pickle=False)
        if (
            data.dtype != np.float32
            or data.ndim != 3
            or data.shape[2] != len(metadata.bands)
            or len(metadata.bands) not in {3, 4}
        ):
            raise RasterFormatError("Imagery window dimensions or bands are invalid")
        self.data = data
        self.metadata = metadata
        self.georeference = GeoReference(
            metadata.epsg,
            metadata.origin_x,
            metadata.origin_y,
            metadata.pixel_width,
            metadata.pixel_height,
            metadata.epsg,
        )

    def read_bounds(
        self, bounds: ProjectedBounds
    ) -> tuple[NDArray[np.float32], ProjectedBounds]:
        window = self.georeference.enclosing_window(bounds)
        if (
            window.row + window.height > self.data.shape[0]
            or window.column + window.width > self.data.shape[1]
        ):
            raise RasterFormatError("Requested bounds extend outside the imagery window")
        data = np.asarray(
            self.data[
                window.row : window.row + window.height,
                window.column : window.column + window.width,
            ],
            dtype=np.float32,
        )
        return data, self.georeference.window_bounds(window)


def write_imagery_window(
    path: Path,
    data: NDArray[np.float32],
    bounds: ProjectedBounds,
    nodata: float,
    bands: tuple[str, ...],
) -> None:
    """Atomically publish one RGB or RGBNIR array with exact outer bounds."""

    if (
        path.suffix.lower() != ".npy"
        or data.dtype != np.float32
        or data.ndim != 3
        or data.shape[2] != len(bands)
        or len(bands) not in {3, 4}
    ):
        raise RasterFormatError("Imagery output must be a Float32 RGB or RGBNIR .npy array")
    metadata = ImageryWindowMetadata(
        bounds.epsg,
        bounds.west,
        bounds.north,
        (bounds.east - bounds.west) / data.shape[1],
        -(bounds.north - bounds.south) / data.shape[0],
        nodata,
        bands,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path = path.with_suffix(path.suffix + ".json")
    if path.exists() or metadata_path.exists():
        raise RasterFormatError("Refusing to overwrite an imagery window artifact")
    array_part = path.with_name(path.name + ".part")
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


def imagery_window_is_valid(path: Path) -> bool:
    try:
        ImageryWindowReader(path)
    except (OSError, ValueError, RasterFormatError):
        return False
    return True


def _read_metadata(path: Path) -> ImageryWindowMetadata:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.pop("schema_version") != SCHEMA_VERSION:
            raise ValueError
        payload["bands"] = tuple(payload["bands"])
        return ImageryWindowMetadata(**payload)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise RasterFormatError("Imagery window metadata is invalid") from exc
