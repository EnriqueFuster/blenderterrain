from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from pathlib import Path

import numpy as np

from blender_terrain.errors import RasterFormatError
from blender_terrain.io.bigtiff_tiles import BigTiffFloatTileReader, PixelWindow
from blender_terrain.io.elevation_mosaic import read_elevation_mosaic
from blender_terrain.models import ProjectedBounds


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
            self.assertEqual(reader.georeference.epsg, 25830)
            self.assertEqual(reader.georeference.origin_x, 99.0)
            self.assertEqual(reader.georeference.origin_y, 201.0)
            self.assertEqual(reader.georeference.pixel_width, 2.0)
            self.assertEqual(reader.georeference.pixel_height, -2.0)
            self.assertEqual(reader.georeference.declared_epsg, 25830)
            self.assertEqual(reader.georeference.bounds(2, 2), (99.0, 197.0, 103.0, 201.0))

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

    def test_converts_aligned_bounds_to_an_exact_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "elevation.tif"
            values = np.arange(16, dtype="<f4").reshape(4, 4)
            _write_minimal_bigtiff(path, values, tile_shape=(2, 2))
            reader = BigTiffFloatTileReader(path)
            bounds = ProjectedBounds(101.0, 195.0, 105.0, 199.0, 25830)

            window = reader.window_for_bounds(bounds)
            data, actual_bounds = reader.read_bounds(bounds)

            self.assertEqual(window, PixelWindow(row=1, column=1, height=2, width=2))
            np.testing.assert_array_equal(data, values[1:3, 1:3])
            self.assertEqual(actual_bounds, bounds)

    def test_expands_unaligned_bounds_to_outer_pixel_edges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "elevation.tif"
            values = np.arange(16, dtype="<f4").reshape(4, 4)
            _write_minimal_bigtiff(path, values, tile_shape=(2, 2))
            reader = BigTiffFloatTileReader(path)
            bounds = ProjectedBounds(101.1, 195.1, 104.9, 198.9, 25830)

            window = reader.window_for_bounds(bounds)
            data, actual_bounds = reader.read_bounds(bounds)

            self.assertEqual(window, PixelWindow(row=1, column=1, height=2, width=2))
            np.testing.assert_array_equal(data, values[1:3, 1:3])
            self.assertEqual(
                actual_bounds, ProjectedBounds(101.0, 195.0, 105.0, 199.0, 25830)
            )

    def test_rejects_bounds_in_another_crs_or_outside_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "elevation.tif"
            _write_minimal_bigtiff(path, np.zeros((2, 2), dtype="<f4"))
            reader = BigTiffFloatTileReader(path)

            with self.assertRaisesRegex(ValueError, "same EPSG"):
                reader.window_for_bounds(ProjectedBounds(99, 197, 101, 199, 25829))
            with self.assertRaisesRegex(ValueError, "outside"):
                reader.window_for_bounds(ProjectedBounds(98, 197, 101, 199, 25830))

    def test_preserves_observed_northing_easting_crs_and_uses_canonical_xy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "elevation.tif"
            _write_minimal_bigtiff(
                path, np.zeros((2, 2), dtype="<f4"), projected_epsg=3042
            )

            georeference = BigTiffFloatTileReader(path).georeference

            self.assertEqual(georeference.declared_epsg, 3042)
            self.assertEqual(georeference.epsg, 25830)

    def test_mosaics_aligned_sources_and_reports_conflicting_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            west_path = Path(directory) / "west.tif"
            east_path = Path(directory) / "east.tif"
            west = np.arange(16, dtype="<f4").reshape(4, 4)
            east = np.arange(100, 116, dtype="<f4").reshape(4, 4)
            _write_minimal_bigtiff(west_path, west, tile_shape=(2, 2))
            _write_minimal_bigtiff(
                east_path,
                east,
                tile_shape=(2, 2),
                projected_epsg=3042,
                model_x=104.0,
            )
            bounds = ProjectedBounds(99.0, 193.0, 111.0, 201.0, 25830)

            mosaic = read_elevation_mosaic(
                (BigTiffFloatTileReader(west_path), BigTiffFloatTileReader(east_path)),
                bounds,
            )

            np.testing.assert_array_equal(mosaic.data[:, :4], west)
            np.testing.assert_array_equal(mosaic.data[:, 4:], east[:, 2:])
            np.testing.assert_array_equal(
                mosaic.source_index,
                np.array([[0, 0, 0, 0, 1, 1]] * 4, dtype=np.int16),
            )
            self.assertEqual(mosaic.overlap_valid_pixels, 8)
            self.assertEqual(mosaic.conflicting_valid_pixels, 8)
            self.assertEqual(mosaic.maximum_overlap_difference, 98.0)

    def test_mosaic_rejects_a_coverage_gap_and_excessive_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            west_path = Path(directory) / "west.tif"
            east_path = Path(directory) / "east.tif"
            values = np.zeros((2, 2), dtype="<f4")
            _write_minimal_bigtiff(west_path, values)
            _write_minimal_bigtiff(east_path, values, model_x=108.0)
            readers = (
                BigTiffFloatTileReader(west_path),
                BigTiffFloatTileReader(east_path),
            )
            bounds = ProjectedBounds(99.0, 197.0, 109.0, 201.0, 25830)

            with self.assertRaisesRegex(RasterFormatError, "do not cover"):
                read_elevation_mosaic(readers, bounds)
            with self.assertRaisesRegex(ValueError, "pixel limit"):
                read_elevation_mosaic(readers, bounds, maximum_pixels=5)

    def test_mosaic_fills_first_source_nodata_from_later_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first_path = Path(directory) / "first.tif"
            second_path = Path(directory) / "second.tif"
            first = np.array([[1.0, -9999.0], [3.0, 4.0]], dtype="<f4")
            second = np.array([[10.0, 20.0], [30.0, 40.0]], dtype="<f4")
            _write_minimal_bigtiff(first_path, first)
            _write_minimal_bigtiff(second_path, second)

            mosaic = read_elevation_mosaic(
                (
                    BigTiffFloatTileReader(first_path),
                    BigTiffFloatTileReader(second_path),
                ),
                ProjectedBounds(99.0, 197.0, 103.0, 201.0, 25830),
            )

            np.testing.assert_array_equal(
                mosaic.data, np.array([[1.0, 20.0], [3.0, 4.0]], dtype=np.float32)
            )
            self.assertEqual(mosaic.source_index[0, 1], 1)
            self.assertEqual(mosaic.overlap_valid_pixels, 3)

    def test_mosaic_rejects_sources_on_different_pixel_grids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first_path = Path(directory) / "first.tif"
            shifted_path = Path(directory) / "shifted.tif"
            values = np.zeros((2, 2), dtype="<f4")
            _write_minimal_bigtiff(first_path, values)
            _write_minimal_bigtiff(shifted_path, values, model_x=100.5)
            readers = (
                BigTiffFloatTileReader(first_path),
                BigTiffFloatTileReader(shifted_path),
            )

            with self.assertRaisesRegex(RasterFormatError, "not aligned"):
                read_elevation_mosaic(
                    readers, ProjectedBounds(99.0, 197.0, 103.0, 201.0, 25830)
                )

    def test_decodes_horizontal_differencing_predictor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "elevation.tif"
            expected = np.array([[1.25, 3.5], [-7.0, 2.0]], dtype="<f4")
            _write_minimal_bigtiff(path, expected, predictor=2)

            actual = BigTiffFloatTileReader(path).read_tile(0, 0)

            np.testing.assert_array_equal(actual, expected)


def _write_minimal_bigtiff(
    path: Path,
    values: np.ndarray,
    predictor: int = 1,
    tile_shape: tuple[int, int] | None = None,
    projected_epsg: int = 25830,
    model_x: float = 100.0,
    model_y: float = 200.0,
) -> None:
    tile_height, tile_width = tile_shape or values.shape
    if values.shape[0] % tile_height or values.shape[1] % tile_width:
        raise ValueError("Test fixture dimensions must be divisible by tile dimensions")
    compressed_tiles = []
    for row in range(0, values.shape[0], tile_height):
        for column in range(0, values.shape[1], tile_width):
            tile = values[row : row + tile_height, column : column + tile_width]
            encoded = tile
            if predictor == 2:
                bits = tile.view("<u4")
                encoded = np.empty_like(bits)
                encoded[:, 0] = bits[:, 0]
                encoded[:, 1:] = bits[:, 1:] - bits[:, :-1]
            compressed_tiles.append(zlib.compress(encoded.tobytes()))
    entry_count = 15
    external_values_offset = 16 + 8 + entry_count * 20 + 8
    pixel_scale = struct.pack("<3d", 2.0, 2.0, 0.0)
    tiepoint = struct.pack("<6d", 0.0, 0.0, 0.0, model_x, model_y, 0.0)
    geo_keys = struct.pack(
        "<16H",
        1,
        1,
        0,
        3,
        1024,
        0,
        1,
        1,
        1025,
        0,
        1,
        2,
        3072,
        0,
        1,
        projected_epsg,
    )
    external_values = pixel_scale + tiepoint + geo_keys
    pixel_scale_offset = external_values_offset
    tiepoint_offset = pixel_scale_offset + len(pixel_scale)
    geo_keys_offset = tiepoint_offset + len(tiepoint)
    index_offset = external_values_offset + len(external_values)
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
        (33550, 12, 3, pixel_scale_offset),
        (33922, 12, 6, tiepoint_offset),
        (34735, 3, 16, geo_keys_offset),
        (42113, 2, 6, int.from_bytes(b"-9999\0\0\0", "little")),
    ]
    entries.sort(key=lambda entry: entry[0])
    header = b"II" + struct.pack("<HHHQ", 43, 8, 0, 16)
    directory = struct.pack("<Q", entry_count)
    directory += b"".join(struct.pack("<HHQQ", *entry) for entry in entries)
    directory += struct.pack("<Q", 0)
    index = struct.pack(f"<{len(tile_offsets)}Q", *tile_offsets)
    index += struct.pack(f"<{len(compressed_tiles)}Q", *(len(tile) for tile in compressed_tiles))
    path.write_bytes(
        header + directory + external_values + index + b"".join(compressed_tiles)
    )
