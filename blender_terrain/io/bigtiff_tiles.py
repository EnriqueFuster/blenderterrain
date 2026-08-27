"""Constrained BigTIFF tile reading for the verified CNIG elevation layout.

This module intentionally supports a narrow format: little-endian BigTIFF, one
Float32 band, tiled storage, Adobe Deflate compression, and either no predictor
or horizontal differencing.
Unsupported layouts fail explicitly so they cannot yield subtly incorrect data.
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray

from ..errors import RasterFormatError
from ..models import ProjectedBounds

_TYPE_SIZES: Final = {1: 1, 2: 1, 3: 2, 4: 4, 12: 8, 16: 8}
_NUMERIC_FORMATS: Final = {1: "B", 3: "H", 4: "I", 12: "d", 16: "Q"}

_IMAGE_WIDTH: Final = 256
_IMAGE_LENGTH: Final = 257
_BITS_PER_SAMPLE: Final = 258
_COMPRESSION: Final = 259
_SAMPLES_PER_PIXEL: Final = 277
_PREDICTOR: Final = 317
_TILE_WIDTH: Final = 322
_TILE_LENGTH: Final = 323
_TILE_OFFSETS: Final = 324
_TILE_BYTE_COUNTS: Final = 325
_SAMPLE_FORMAT: Final = 339
_GDAL_NODATA: Final = 42113
_MODEL_PIXEL_SCALE: Final = 33550
_MODEL_TIEPOINT: Final = 33922
_GEO_KEY_DIRECTORY: Final = 34735

_GT_MODEL_TYPE: Final = 1024
_GT_RASTER_TYPE: Final = 1025
_PROJECTED_CRS_TYPE: Final = 3072

TagValue = tuple[int | float, ...] | str


@dataclass(frozen=True, slots=True)
class TileLayout:
    """Validated pixel and tile dimensions of a supported elevation TIFF."""

    width: int
    height: int
    tile_width: int
    tile_height: int
    nodata: float | None

    @property
    def tile_columns(self) -> int:
        """Return the number of tile columns in the image."""

        return (self.width + self.tile_width - 1) // self.tile_width

    @property
    def tile_rows(self) -> int:
        """Return the number of tile rows in the image."""

        return (self.height + self.tile_height - 1) // self.tile_height


@dataclass(frozen=True, slots=True)
class GeoReference:
    """North-up projected georeferencing expressed at the outer pixel edge."""

    epsg: int
    origin_x: float
    origin_y: float
    pixel_width: float
    pixel_height: float
    declared_epsg: int

    def bounds(self, width: int, height: int) -> tuple[float, float, float, float]:
        """Return west, south, east, and north bounds for image dimensions."""

        east = self.origin_x + width * self.pixel_width
        south = self.origin_y + height * self.pixel_height
        return self.origin_x, south, east, self.origin_y

    def window_bounds(self, window: PixelWindow) -> ProjectedBounds:
        """Return the outer projected bounds of a pixel window."""

        west = self.origin_x + window.column * self.pixel_width
        north = self.origin_y + window.row * self.pixel_height
        east = west + window.width * self.pixel_width
        south = north + window.height * self.pixel_height
        return ProjectedBounds(west, south, east, north, self.epsg)

    def enclosing_window(self, bounds: ProjectedBounds) -> PixelWindow:
        """Return the smallest grid window that fully contains projected bounds."""

        if bounds.epsg != self.epsg:
            raise ValueError("Projected bounds and raster grid must use the same EPSG code")
        pixel_height = -self.pixel_height
        left = (bounds.west - self.origin_x) / self.pixel_width
        right = (bounds.east - self.origin_x) / self.pixel_width
        top = (self.origin_y - bounds.north) / pixel_height
        bottom = (self.origin_y - bounds.south) / pixel_height
        column = _floor_grid_coordinate(left)
        row = _floor_grid_coordinate(top)
        column_end = _ceil_grid_coordinate(right)
        row_end = _ceil_grid_coordinate(bottom)
        return PixelWindow(row, column, row_end - row, column_end - column)


@dataclass(frozen=True, slots=True)
class PixelWindow:
    """Integer pixel window using a top-left row and column origin."""

    row: int
    column: int
    height: int
    width: int

    def __post_init__(self) -> None:
        if self.row < 0 or self.column < 0:
            raise ValueError("Pixel window origin must be non-negative")
        if self.height <= 0 or self.width <= 0:
            raise ValueError("Pixel window dimensions must be positive")


class BigTiffFloatTileReader:
    """Read one compressed Float32 tile at a time from a verified CNIG TIFF."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file_size = path.stat().st_size
        self._byte_order, first_ifd_offset = self._read_header()
        tags = self._read_first_directory(first_ifd_offset)
        self._predictor = _single_value(tags, _PREDICTOR)
        self.layout = self._validate_layout(tags)
        self.georeference = _parse_georeference(tags)
        self._tile_offsets = self._required_values(tags, _TILE_OFFSETS)
        self._tile_byte_counts = self._required_values(tags, _TILE_BYTE_COUNTS)
        expected_tiles = self.layout.tile_columns * self.layout.tile_rows
        if (
            len(self._tile_offsets) != expected_tiles
            or len(self._tile_byte_counts) != expected_tiles
        ):
            raise RasterFormatError("TIFF tile index does not match its image dimensions")

    def read_tile(self, row: int, column: int) -> NDArray[np.float32]:
        """Return one image tile cropped to valid pixels at its outer edges."""

        if not (
            0 <= row < self.layout.tile_rows and 0 <= column < self.layout.tile_columns
        ):
            raise IndexError("Tile coordinates are outside the image")
        index = row * self.layout.tile_columns + column
        offset = self._tile_offsets[index]
        byte_count = self._tile_byte_counts[index]
        if offset < 0 or byte_count <= 0 or offset + byte_count > self._file_size:
            raise RasterFormatError("TIFF tile points outside the source file")

        with self._path.open("rb") as stream:
            stream.seek(offset)
            compressed = stream.read(byte_count)
        if len(compressed) != byte_count:
            raise RasterFormatError("TIFF tile is truncated")

        expected_bytes = self.layout.tile_width * self.layout.tile_height * 4
        raw = _inflate_exact(compressed, expected_bytes)
        if self._predictor == 2:
            differences = np.frombuffer(
                raw, dtype=np.dtype(f"{self._byte_order}u4")
            ).reshape(self.layout.tile_height, self.layout.tile_width)
            tile = np.cumsum(differences, axis=1, dtype=np.uint32).view(
                np.dtype(f"{self._byte_order}f4")
            )
        else:
            tile = np.frombuffer(raw, dtype=np.dtype(f"{self._byte_order}f4")).reshape(
                self.layout.tile_height, self.layout.tile_width
            )
        valid_height = min(
            self.layout.tile_height, self.layout.height - row * self.layout.tile_height
        )
        valid_width = min(
            self.layout.tile_width, self.layout.width - column * self.layout.tile_width
        )
        return tile[:valid_height, :valid_width].copy()

    def read_window(
        self, row: int, column: int, height: int, width: int
    ) -> NDArray[np.float32]:
        """Return a pixel window by decoding only the tiles that intersect it."""

        if row < 0 or column < 0 or height <= 0 or width <= 0:
            raise ValueError("Window origin must be non-negative and dimensions must be positive")
        if row + height > self.layout.height or column + width > self.layout.width:
            raise ValueError("Window extends outside the image")

        result = np.empty((height, width), dtype=np.float32)
        first_tile_row = row // self.layout.tile_height
        last_tile_row = (row + height - 1) // self.layout.tile_height
        first_tile_column = column // self.layout.tile_width
        last_tile_column = (column + width - 1) // self.layout.tile_width
        for tile_row in range(first_tile_row, last_tile_row + 1):
            tile_top = tile_row * self.layout.tile_height
            for tile_column in range(first_tile_column, last_tile_column + 1):
                tile_left = tile_column * self.layout.tile_width
                tile = self.read_tile(tile_row, tile_column)
                intersection_top = max(row, tile_top)
                intersection_left = max(column, tile_left)
                intersection_bottom = min(row + height, tile_top + tile.shape[0])
                intersection_right = min(column + width, tile_left + tile.shape[1])
                result[
                    intersection_top - row : intersection_bottom - row,
                    intersection_left - column : intersection_right - column,
                ] = tile[
                    intersection_top - tile_top : intersection_bottom - tile_top,
                    intersection_left - tile_left : intersection_right - tile_left,
                ]
        return result

    def window_for_bounds(self, bounds: ProjectedBounds) -> PixelWindow:
        """Return the smallest source window that fully contains projected bounds."""

        if bounds.epsg != self.georeference.epsg:
            raise ValueError("Projected bounds and raster must use the same EPSG code")
        raster_west, raster_south, raster_east, raster_north = self.georeference.bounds(
            self.layout.width, self.layout.height
        )
        if (
            bounds.west < raster_west
            or bounds.south < raster_south
            or bounds.east > raster_east
            or bounds.north > raster_north
        ):
            raise ValueError("Projected bounds extend outside the raster")

        return self.georeference.enclosing_window(bounds)

    def read_bounds(
        self, bounds: ProjectedBounds
    ) -> tuple[NDArray[np.float32], ProjectedBounds]:
        """Read enclosing pixels and return their exact projected outer bounds."""

        window = self.window_for_bounds(bounds)
        data = self.read_window(window.row, window.column, window.height, window.width)
        return data, self.georeference.window_bounds(window)

    def _read_header(self) -> tuple[str, int]:
        with self._path.open("rb") as stream:
            header = stream.read(16)
        if len(header) != 16 or header[:2] != b"II":
            raise RasterFormatError("Only little-endian BigTIFF files are supported")
        version, offset_size, reserved, first_ifd_offset = struct.unpack("<HHHQ", header[2:])
        if version != 43 or offset_size != 8 or reserved != 0:
            raise RasterFormatError("Only the standard BigTIFF header layout is supported")
        if not 16 <= first_ifd_offset < self._file_size:
            raise RasterFormatError("BigTIFF first directory offset is invalid")
        return "<", first_ifd_offset

    def _read_first_directory(self, offset: int) -> dict[int, TagValue]:
        with self._path.open("rb") as stream:
            stream.seek(offset)
            count_bytes = stream.read(8)
            if len(count_bytes) != 8:
                raise RasterFormatError("BigTIFF directory is truncated")
            entry_count = struct.unpack("<Q", count_bytes)[0]
            directory_size = 8 + entry_count * 20 + 8
            if offset + directory_size > self._file_size:
                raise RasterFormatError("BigTIFF directory points outside the source file")
            entries = stream.read(entry_count * 20)

        tags: dict[int, TagValue] = {}
        for index in range(entry_count):
            entry = entries[index * 20 : (index + 1) * 20]
            tag, value_type, value_count, value_or_offset = struct.unpack("<HHQQ", entry)
            if value_type not in _TYPE_SIZES:
                continue
            value_size = _TYPE_SIZES[value_type] * value_count
            if value_size > self._file_size:
                raise RasterFormatError("BigTIFF tag has an unreasonable value size")
            if value_size <= 8:
                encoded = entry[12 : 12 + value_size]
            else:
                if value_or_offset + value_size > self._file_size:
                    raise RasterFormatError("BigTIFF tag points outside the source file")
                with self._path.open("rb") as stream:
                    stream.seek(value_or_offset)
                    encoded = stream.read(value_size)
            tags[tag] = _decode_value(value_type, value_count, encoded)
        return tags

    def _validate_layout(self, tags: dict[int, TagValue]) -> TileLayout:
        width = _single_value(tags, _IMAGE_WIDTH)
        height = _single_value(tags, _IMAGE_LENGTH)
        tile_width = _single_value(tags, _TILE_WIDTH)
        tile_height = _single_value(tags, _TILE_LENGTH)
        if any(value <= 0 for value in (width, height, tile_width, tile_height)):
            raise RasterFormatError("TIFF image and tile dimensions must be positive")
        if _single_value(tags, _BITS_PER_SAMPLE) != 32:
            raise RasterFormatError("Only Float32 TIFF samples are supported")
        if _single_value(tags, _COMPRESSION) != 8:
            raise RasterFormatError("Only Adobe Deflate TIFF compression is supported")
        if _single_value(tags, _SAMPLES_PER_PIXEL) != 1:
            raise RasterFormatError("Only single-band TIFF images are supported")
        if _single_value(tags, _PREDICTOR) not in {1, 2}:
            raise RasterFormatError("Only TIFF predictors 1 and 2 are supported")
        if _single_value(tags, _SAMPLE_FORMAT) != 3:
            raise RasterFormatError("Only IEEE floating-point TIFF samples are supported")
        raw_nodata = tags.get(_GDAL_NODATA)
        nodata = float(raw_nodata.rstrip("\x00")) if isinstance(raw_nodata, str) else None
        return TileLayout(width, height, tile_width, tile_height, nodata)

    @staticmethod
    def _required_values(tags: dict[int, TagValue], tag: int) -> tuple[int, ...]:
        value = tags.get(tag)
        if not isinstance(value, tuple) or not all(isinstance(item, int) for item in value):
            raise RasterFormatError(f"BigTIFF required tag {tag} is missing or invalid")
        return tuple(item for item in value if isinstance(item, int))


def _decode_value(value_type: int, count: int, encoded: bytes) -> TagValue:
    if value_type == 2:
        return encoded.decode("ascii", errors="strict")
    return struct.unpack(f"<{count}{_NUMERIC_FORMATS[value_type]}", encoded)


def _single_value(tags: dict[int, TagValue], tag: int) -> int:
    values = tags.get(tag)
    if (
        not isinstance(values, tuple)
        or len(values) != 1
        or not isinstance(values[0], int)
    ):
        raise RasterFormatError(f"BigTIFF required tag {tag} is missing or invalid")
    return values[0]


def _parse_georeference(tags: dict[int, TagValue]) -> GeoReference:
    scale = _numeric_values(tags, _MODEL_PIXEL_SCALE, 3)
    tiepoint = _numeric_values(tags, _MODEL_TIEPOINT, 6)
    if scale[0] <= 0 or scale[1] <= 0:
        raise RasterFormatError("GeoTIFF pixel scale must be positive")
    if scale[2] != 0 or tiepoint[2] != 0 or tiepoint[5] != 0:
        raise RasterFormatError("GeoTIFF vertical model coordinates are not supported")

    keys = _parse_geo_keys(tags)
    if keys.get(_GT_MODEL_TYPE) != 1:
        raise RasterFormatError("Only projected GeoTIFF coordinate systems are supported")
    raster_type = keys.get(_GT_RASTER_TYPE)
    if raster_type not in {1, 2}:
        raise RasterFormatError("GeoTIFF raster type is missing or unsupported")
    declared_epsg = keys.get(_PROJECTED_CRS_TYPE)
    if declared_epsg is None or declared_epsg == 32767:
        raise RasterFormatError("GeoTIFF has no directly encoded projected EPSG code")
    epsg = _canonical_xy_epsg(declared_epsg)

    origin_x = tiepoint[3] - tiepoint[0] * scale[0]
    origin_y = tiepoint[4] + tiepoint[1] * scale[1]
    if raster_type == 2:
        origin_x -= scale[0] / 2
        origin_y += scale[1] / 2
    return GeoReference(
        epsg=epsg,
        origin_x=origin_x,
        origin_y=origin_y,
        pixel_width=scale[0],
        pixel_height=-scale[1],
        declared_epsg=declared_epsg,
    )


def _numeric_values(tags: dict[int, TagValue], tag: int, count: int) -> tuple[int | float, ...]:
    values = tags.get(tag)
    if not isinstance(values, tuple) or len(values) != count:
        raise RasterFormatError(f"GeoTIFF required tag {tag} is missing or invalid")
    return values


def _parse_geo_keys(tags: dict[int, TagValue]) -> dict[int, int]:
    raw_directory = tags.get(_GEO_KEY_DIRECTORY)
    if not isinstance(raw_directory, tuple) or not all(
        isinstance(value, int) for value in raw_directory
    ):
        raise RasterFormatError("GeoTIFF key directory is missing or invalid")
    directory = tuple(raw_directory)
    if len(directory) < 4 or directory[0:3] != (1, 1, 0):
        raise RasterFormatError("GeoTIFF key directory header is unsupported")
    key_count = int(directory[3])
    if len(directory) != 4 + key_count * 4:
        raise RasterFormatError("GeoTIFF key directory length is invalid")
    keys: dict[int, int] = {}
    for index in range(key_count):
        key, location, count, value = directory[4 + index * 4 : 8 + index * 4]
        if location == 0 and count == 1:
            keys[int(key)] = int(value)
    return keys


def _inflate_exact(compressed: bytes, expected_size: int) -> bytes:
    decompressor = zlib.decompressobj()
    raw = decompressor.decompress(compressed, expected_size + 1)
    raw += decompressor.flush(expected_size + 1 - len(raw))
    if len(raw) != expected_size or decompressor.unconsumed_tail or decompressor.unused_data:
        raise RasterFormatError("TIFF tile does not decompress to its expected size")
    return raw


def _floor_grid_coordinate(value: float) -> int:
    nearest = round(value)
    return nearest if abs(value - nearest) <= 1e-9 else math.floor(value)


def _ceil_grid_coordinate(value: float) -> int:
    nearest = round(value)
    return nearest if abs(value - nearest) <= 1e-9 else math.ceil(value)


def _canonical_xy_epsg(declared_epsg: int) -> int:
    if declared_epsg == 3042:
        return 25830
    return declared_epsg
