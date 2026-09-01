"""Small dependency-free GeoTIFF writer for prepared BlenderTerrain rasters."""

from __future__ import annotations

import os
import struct
import zlib
from collections.abc import Callable
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from ..errors import RasterFormatError
from ..models import ProjectedBounds
from .atomic import finalize_part

_TILE_SIZE = 256


def write_geotiff(
    path: Path,
    data: NDArray[np.float32] | NDArray[np.uint8],
    bounds: ProjectedBounds,
    *,
    nodata: float | int | None = None,
    pixel_is_point: bool = False,
    progress_callback: Callable[[float], None] | None = None,
) -> None:
    """Atomically write a tiled Deflate BigTIFF with direct EPSG georeferencing."""

    if data.dtype not in {np.dtype(np.float32), np.dtype(np.uint8)}:
        raise RasterFormatError("GeoTIFF export supports only Float32 and UInt8 data")
    if data.ndim not in {2, 3} or (data.ndim == 3 and data.shape[2] != 3):
        raise RasterFormatError("GeoTIFF export supports one-band or RGB arrays")
    height, width = data.shape[:2]
    if min(height, width) < 1:
        raise RasterFormatError("GeoTIFF dimensions must be positive")
    if pixel_is_point and min(height, width) < 2:
        raise RasterFormatError("Point-sampled GeoTIFF requires at least two samples per axis")
    if path.suffix.lower() not in {".tif", ".tiff"}:
        raise RasterFormatError("GeoTIFF output must use a .tif or .tiff extension")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise RasterFormatError(f"Refusing to overwrite GeoTIFF: {path.name}")

    part = path.with_name(path.name + ".part")
    try:
        _write_bigtiff(
            part,
            data,
            bounds,
            nodata=nodata,
            pixel_is_point=pixel_is_point,
            progress_callback=progress_callback,
        )
        finalize_part(part, path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def _write_bigtiff(
    path: Path,
    data: NDArray[np.float32] | NDArray[np.uint8],
    bounds: ProjectedBounds,
    *,
    nodata: float | int | None,
    pixel_is_point: bool,
    progress_callback: Callable[[float], None] | None,
) -> None:
    height, width = data.shape[:2]
    samples = 1 if data.ndim == 2 else data.shape[2]
    tile_rows = (height + _TILE_SIZE - 1) // _TILE_SIZE
    tile_columns = (width + _TILE_SIZE - 1) // _TILE_SIZE
    tile_count = tile_rows * tile_columns
    compressed_tiles: list[bytes] = []
    for tile_row in range(tile_rows):
        for tile_column in range(tile_columns):
            tile_shape = (_TILE_SIZE, _TILE_SIZE, samples) if samples > 1 else (
                _TILE_SIZE,
                _TILE_SIZE,
            )
            tile = np.zeros(tile_shape, dtype=data.dtype)
            row = tile_row * _TILE_SIZE
            column = tile_column * _TILE_SIZE
            source = data[
                row : min(row + _TILE_SIZE, height),
                column : min(column + _TILE_SIZE, width),
            ]
            tile[: source.shape[0], : source.shape[1]] = source
            encoded = _horizontal_difference(tile)
            compressed_tiles.append(zlib.compress(encoded.tobytes(), level=6))
            if progress_callback is not None:
                progress_callback(len(compressed_tiles) / tile_count * 0.7)

    scale_x = (bounds.east - bounds.west) / (width - 1 if pixel_is_point else width)
    scale_y = (bounds.north - bounds.south) / (height - 1 if pixel_is_point else height)
    pixel_scale = struct.pack("<3d", scale_x, scale_y, 0.0)
    tiepoint = struct.pack("<6d", 0.0, 0.0, 0.0, bounds.west, bounds.north, 0.0)
    model_type = 2 if 4000 <= bounds.epsg < 5000 else 1
    crs_key = 2048 if model_type == 2 else 3072
    geo_keys = struct.pack(
        "<16H",
        1,
        1,
        0,
        3,
        1024,
        0,
        1,
        model_type,
        1025,
        0,
        1,
        2 if pixel_is_point else 1,
        crs_key,
        0,
        1,
        bounds.epsg,
    )
    nodata_bytes = None if nodata is None else f"{nodata:g}\0".encode("ascii")
    entry_count = 16 + (1 if nodata_bytes is not None else 0)
    external_offset = 16 + 8 + entry_count * 20 + 8
    external = pixel_scale + tiepoint + geo_keys
    scale_offset = external_offset
    tiepoint_offset = scale_offset + len(pixel_scale)
    keys_offset = tiepoint_offset + len(tiepoint)
    nodata_offset = keys_offset + len(geo_keys)
    if nodata_bytes is not None and len(nodata_bytes) > 8:
        external += nodata_bytes
    index_offset = external_offset + len(external)
    offsets_index = index_offset
    byte_counts_index = offsets_index + tile_count * 8
    data_offset = byte_counts_index + tile_count * 8
    tile_offsets: list[int] = []
    next_offset = data_offset
    for compressed in compressed_tiles:
        tile_offsets.append(next_offset)
        next_offset += len(compressed)

    bits = data.dtype.itemsize * 8
    sample_format = 3 if data.dtype == np.float32 else 1
    entries = [
        (256, 16, 1, width),
        (257, 16, 1, height),
        (258, 3, samples, _inline_shorts((bits,) * samples)),
        (259, 3, 1, 8),
        (262, 3, 1, 2 if samples == 3 else 1),
        (277, 3, 1, samples),
        (284, 3, 1, 1),
        (317, 3, 1, 2),
        (322, 16, 1, _TILE_SIZE),
        (323, 16, 1, _TILE_SIZE),
        (324, 16, tile_count, tile_offsets[0] if tile_count == 1 else offsets_index),
        (
            325,
            16,
            tile_count,
            len(compressed_tiles[0]) if tile_count == 1 else byte_counts_index,
        ),
        (339, 3, samples, _inline_shorts((sample_format,) * samples)),
        (33550, 12, 3, scale_offset),
        (33922, 12, 6, tiepoint_offset),
        (34735, 3, 16, keys_offset),
    ]
    if nodata_bytes is not None:
        nodata_value = (
            int.from_bytes(nodata_bytes.ljust(8, b"\0"), "little")
            if len(nodata_bytes) <= 8
            else nodata_offset
        )
        entries.append((42113, 2, len(nodata_bytes), nodata_value))
    entries.sort(key=lambda entry: entry[0])
    directory = struct.pack("<Q", len(entries))
    directory += b"".join(struct.pack("<HHQQ", *entry) for entry in entries)
    directory += struct.pack("<Q", 0)
    index = struct.pack(f"<{tile_count}Q", *tile_offsets)
    index += struct.pack(
        f"<{tile_count}Q", *(len(compressed) for compressed in compressed_tiles)
    )
    with path.open("xb") as stream:
        stream.write(b"II" + struct.pack("<HHHQ", 43, 8, 0, 16))
        stream.write(directory)
        stream.write(external)
        stream.write(index)
        for index_, compressed in enumerate(compressed_tiles, start=1):
            stream.write(compressed)
            if progress_callback is not None:
                progress_callback(0.7 + index_ / tile_count * 0.3)
        stream.flush()
        os.fsync(stream.fileno())


def _horizontal_difference(data: NDArray[np.float32] | NDArray[np.uint8]) -> NDArray[np.uint8]:
    bytes_per_sample = data.dtype.itemsize
    unsigned_dtype = {1: np.uint8, 4: np.uint32}[bytes_per_sample]
    unsigned = data.view(unsigned_dtype)
    encoded = np.empty_like(unsigned)
    encoded[:, 0] = unsigned[:, 0]
    encoded[:, 1:] = unsigned[:, 1:] - unsigned[:, :-1]
    return encoded.view(np.uint8)


def _inline_shorts(values: tuple[int, ...]) -> int:
    return int.from_bytes(struct.pack(f"<{len(values)}H", *values).ljust(8, b"\0"), "little")
