"""Strict little-endian Float32 BIL windows with executable provenance."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from ..errors import ProviderContractChanged, RasterFormatError
from ..models import ProjectedBounds
from .atomic import finalize_part
from .bigtiff_tiles import GeoReference, TileLayout

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class Bil32Metadata:
    provider_id: str
    product_id: str
    endpoint: str
    wms_version: str
    layer: str
    style: str
    format: str
    crs_epsg: int
    bbox: tuple[float, float, float, float]
    width: int
    height: int
    dtype: str
    row_order: str
    nodata: float
    retrieved_at_utc: str
    response_sha256: str

    def __post_init__(self) -> None:
        if (
            not self.provider_id
            or not self.product_id
            or not self.endpoint
            or not self.layer
            or self.wms_version != "1.3.0"
            or self.dtype != "<f4"
            or self.row_order != "north_to_south"
            or self.width <= 0
            or self.height <= 0
            or self.crs_epsg <= 0
            or len(self.response_sha256) != 64
            or not all(math.isfinite(value) for value in (*self.bbox, self.nodata))
        ):
            raise RasterFormatError("BIL32 provenance is invalid")
        west, south, east, north = self.bbox
        if west >= east or south >= north:
            raise RasterFormatError("BIL32 bounds are invalid")


class Bil32WindowReader:
    """Memory-map one north-up BIL32 response with verified provenance."""

    def __init__(self, path: Path, *, verify_hash: bool = False) -> None:
        metadata = read_bil32_metadata(path)
        expected_bytes = metadata.width * metadata.height * 4
        if path.stat().st_size != expected_bytes:
            raise RasterFormatError("BIL32 byte length does not match its dimensions")
        if verify_hash and _sha256(path) != metadata.response_sha256:
            raise RasterFormatError("BIL32 response hash does not match its provenance")
        west, south, east, north = metadata.bbox
        self._data = np.memmap(
            path,
            dtype="<f4",
            mode="r",
            shape=(metadata.height, metadata.width),
        )
        self.layout = TileLayout(
            metadata.width,
            metadata.height,
            metadata.width,
            metadata.height,
            metadata.nodata,
        )
        self.georeference = GeoReference(
            metadata.crs_epsg,
            west,
            north,
            (east - west) / metadata.width,
            -(north - south) / metadata.height,
            metadata.crs_epsg,
        )

    @property
    def nodata(self) -> float:
        nodata = self.layout.nodata
        if nodata is None:
            raise RasterFormatError("BIL32 window has no NoData value")
        return nodata

    def read_bounds(
        self, bounds: ProjectedBounds
    ) -> tuple[NDArray[np.float32], ProjectedBounds]:
        window = self.georeference.enclosing_window(bounds)
        if (
            window.row + window.height > self.layout.height
            or window.column + window.width > self.layout.width
        ):
            raise RasterFormatError("Requested bounds extend outside the BIL32 window")
        data = np.asarray(
            self._data[
                window.row : window.row + window.height,
                window.column : window.column + window.width,
            ],
            dtype=np.float32,
        )
        return data, self.georeference.window_bounds(window)


def validate_bil32_payload(
    payload: bytes, width: int, height: int, nodata: float
) -> NDArray[np.float32]:
    """Decode a complete BIL32 response or reject a changed provider contract."""

    if width <= 0 or height <= 0 or len(payload) != width * height * 4:
        raise ProviderContractChanged("BIL32 response length does not match the request")
    data = np.frombuffer(payload, dtype="<f4").reshape(height, width)
    valid = data[data != nodata]
    if valid.size and not np.isfinite(valid).all():
        raise ProviderContractChanged("BIL32 response contains non-finite samples")
    return data


def validate_bil32_file(path: Path, width: int, height: int, nodata: float) -> str:
    """Validate a downloaded BIL32 without loading the complete window into RAM."""

    if width <= 0 or height <= 0 or path.stat().st_size != width * height * 4:
        raise ProviderContractChanged("BIL32 response length does not match the request")
    data = np.memmap(path, dtype="<f4", mode="r", shape=(height, width))
    for start in range(0, height, 256):
        block = np.asarray(data[start : start + 256])
        valid = block[block != nodata]
        if valid.size and not np.isfinite(valid).all():
            raise ProviderContractChanged("BIL32 response contains non-finite samples")
    del data
    return _sha256(path)


def write_bil32_metadata(path: Path, metadata: Bil32Metadata) -> Path:
    """Atomically create the provenance sidecar adjacent to an existing BIL."""

    destination = _metadata_path(path)
    part = destination.with_name(destination.name + ".part")
    if destination.exists() or part.exists():
        raise RasterFormatError("Refusing to overwrite BIL32 provenance")
    encoded = json.dumps(
        {"schema_version": SCHEMA_VERSION, **asdict(metadata)},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    try:
        with part.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        finalize_part(part, destination)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return destination


def read_bil32_metadata(path: Path) -> Bil32Metadata:
    try:
        payload = json.loads(_metadata_path(path).read_text(encoding="utf-8"))
        if payload.pop("schema_version") != SCHEMA_VERSION:
            raise ValueError
        payload["bbox"] = tuple(payload["bbox"])
        return Bil32Metadata(**payload)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise RasterFormatError("BIL32 provenance is invalid") from exc


def _metadata_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
