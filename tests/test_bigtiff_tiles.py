from __future__ import annotations

from pathlib import Path
import struct
import tempfile
import unittest
import zlib

import numpy as np

from blender_terrain.errors import RasterFormatError
from blender_terrain.io.bigtiff_tiles import BigTiffFloatTileReader


class BigTiffTilesTests(unittest.TestCase):
    def test_reads_a_single_compressed_tile_and_nodata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "elevation.tif"
            expected = np.array([[10.5, 11.5], [12.5, -9999.0]], dtype="<f4")
            _write_minimal_bigtiff(path, expected)

            reader = BigTiffFloatTileReader(path)

            np.testing.assert_array_equal(reader.read_tile(0, 0), expected)
            self.assertEqual(reader.layout.nodata, -9999.0)
            self.assertEqual(reader.layout.tile_rows, 1)
            self.assertEqual(reader.layout.tile_columns, 1)

    def test_rejects_tiles_outside_the_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "elevation.tif"
            _write_minimal_bigtiff(path, np.zeros((2, 2), dtype="<f4"))
            reader = BigTiffFloatTileReader(path)

            with self.assertRaises(IndexError):
                reader.read_tile(1, 0)

    def test_reads_a_window_across_tile_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "elevation.tif"
            values = np.arange(16, dtype="<f4").reshape(4, 4)
            _write_minimal_bigtiff(path, values, tile_shape=(2, 2))
            reader = BigTiffFloatTileReader(path)

            np.testing.assert_array_equal(reader.read_window(1, 1, 3, 3), values[1:4, 1:4])

            with self.assertRaisesRegex(ValueError, "outside"):
                reader.read_window(3, 3, 2, 2)

    def test_rejects_an_unsupported_predictor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "elevation.tif"
            _write_minimal_bigtiff(path, np.zeros((2, 2), dtype="<f4"), predictor=2)

            with self.assertRaisesRegex(RasterFormatError, "predictor"):
                BigTiffFloatTileReader(path)


def _write_minimal_bigtiff(
    path: Path,
    values: np.ndarray,
    predictor: int = 1,
    tile_shape: tuple[int, int] | None = None,
) -> None:
    tile_height, tile_width = tile_shape or values.shape
    if values.shape[0] % tile_height or values.shape[1] % tile_width:
        raise ValueError("Test fixture dimensions must be divisible by tile dimensions")
    compressed_tiles = [
        zlib.compress(values[row : row + tile_height, column : column + tile_width].tobytes())
        for row in range(0, values.shape[0], tile_height)
        for column in range(0, values.shape[1], tile_width)
    ]
    entry_count = 12
    index_offset = 16 + 8 + entry_count * 20 + 8
    tile_offsets_offset = index_offset
    tile_byte_counts_offset = tile_offsets_offset + len(compressed_tiles) * 8
    data_offset = tile_byte_counts_offset + len(compressed_tiles) * 8
    tile_offsets: list[int] = []
    next_offset = data_offset
    for compressed in compressed_tiles:
        tile_offsets.append(next_offset)
        next_offset += len(compressed)
    tile_offsets_value = tile_offsets[0] if len(tile_offsets) == 1 else tile_offsets_offset
    tile_byte_counts_value = (
        len(compressed_tiles[0]) if len(compressed_tiles) == 1 else tile_byte_counts_offset
    )
    entries = [
        (256, 16, 1, values.shape[1]),
        (257, 16, 1, values.shape[0]),
        (258, 3, 1, 32),
        (259, 3, 1, 8),
        (277, 3, 1, 1),
        (317, 3, 1, predictor),
        (322, 16, 1, tile_width),
        (323, 16, 1, tile_height),
        (324, 16, len(compressed_tiles), tile_offsets_value),
        (325, 16, len(compressed_tiles), tile_byte_counts_value),
        (339, 3, 1, 3),
        (42113, 2, 6, int.from_bytes(b"-9999\0\0\0", "little")),
    ]
    header = b"II" + struct.pack("<HHHQ", 43, 8, 0, 16)
    directory = struct.pack("<Q", entry_count)
    directory += b"".join(struct.pack("<HHQQ", *entry) for entry in entries)
    directory += struct.pack("<Q", 0)
    index = struct.pack(f"<{len(tile_offsets)}Q", *tile_offsets)
    index += struct.pack(f"<{len(compressed_tiles)}Q", *(len(tile) for tile in compressed_tiles))
    path.write_bytes(header + directory + index + b"".join(compressed_tiles))
