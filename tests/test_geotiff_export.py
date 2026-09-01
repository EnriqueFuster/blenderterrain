from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from blender_terrain.core import export_prepared_rasters
from blender_terrain.io.bigtiff_tiles import BigTiffFloatTileReader
from blender_terrain.io.geotiff import write_geotiff
from blender_terrain.io.png_validation import write_rgb_png
from blender_terrain.models import ProjectedBounds


def test_writes_float_point_grid_with_epsg_and_nodata(tmp_path: Path) -> None:
    path = tmp_path / "elevation.tif"
    values = np.arange(12, dtype=np.float32).reshape(3, 4)
    bounds = ProjectedBounds(100.0, 200.0, 106.0, 204.0, 25830)

    write_geotiff(path, values, bounds, nodata=-9999.0, pixel_is_point=True)

    reader = BigTiffFloatTileReader(path)
    np.testing.assert_array_equal(reader.read_tile(0, 0), values)
    assert reader.nodata == -9999.0
    assert reader.georeference.epsg == 25830
    assert reader.georeference.pixel_width == 2.0
    assert reader.georeference.pixel_height == -2.0
    assert reader.georeference.bounds(4, 3) == (99.0, 199.0, 107.0, 205.0)


def test_writes_rgb_area_grid(tmp_path: Path) -> None:
    path = tmp_path / "imagery.tif"
    values = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
    bounds = ProjectedBounds(10.0, 20.0, 16.0, 24.0, 25830)

    write_geotiff(path, values, bounds)

    reader = BigTiffFloatTileReader(path)
    np.testing.assert_array_equal(reader.read_tile(0, 0), values.astype(np.float32))
    assert reader.georeference.bounds(3, 2) == (10.0, 20.0, 16.0, 24.0)


def test_writes_and_reads_multiple_internal_tiles(tmp_path: Path) -> None:
    path = tmp_path / "large-mask.tif"
    values = np.arange(270 * 260, dtype=np.uint8).reshape(270, 260)
    bounds = ProjectedBounds(0.0, 0.0, 260.0, 270.0, 25830)

    write_geotiff(path, values, bounds)

    reader = BigTiffFloatTileReader(path)
    assert reader.layout.tile_columns == 2
    assert reader.layout.tile_rows == 2
    np.testing.assert_array_equal(reader.read_tile(0, 0), values[:256, :256])
    np.testing.assert_array_equal(reader.read_tile(1, 1), values[256:, 256:])


def test_exports_delivery_rasters_and_provenance(tmp_path: Path) -> None:
    elevation = np.arange(9, dtype=np.float32).reshape(3, 3)
    mask = np.arange(9, dtype=np.uint8).reshape(3, 3) % 2
    imagery = np.arange(27, dtype=np.uint8).reshape(3, 3, 3)
    elevation_path = tmp_path / "elevation.npy"
    mask_path = tmp_path / "mask.npy"
    imagery_path = tmp_path / "imagery.png"
    np.save(elevation_path, elevation, allow_pickle=False)
    np.save(mask_path, mask, allow_pickle=False)
    write_rgb_png(imagery_path, imagery)
    bounds = {"west": 100.0, "south": 200.0, "east": 104.0, "north": 204.0, "epsg": 25830}
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "import_id": "12345678-1234-4234-8234-123456789abc",
                "request": {"product": "MDT02"},
                "crs": [{"epsg": 25830}],
                "sources": [],
                "provenance": {"source": "test"},
                "processed_elevation": [
                    {
                        "path": str(elevation_path),
                        "marine_mask_path": str(mask_path),
                        "bounds": bounds,
                        "nodata": -9999.0,
                    }
                ],
                "imagery": [{"path": str(imagery_path), "bounds": bounds}],
            }
        ),
        encoding="utf-8",
    )
    progress: list[float] = []

    exported = export_prepared_rasters(
        result_path, tmp_path / "exports", lambda value, _message: progress.append(value)
    )

    assert [path.name for path in exported.paths] == [
        "001_elevation_elevation.tif",
        "002_marine_mask_mask.tif",
        "003_imagery_imagery.tif",
        "provenance.json",
    ]
    np.testing.assert_array_equal(
        BigTiffFloatTileReader(exported.paths[0]).read_tile(0, 0), elevation
    )
    np.testing.assert_array_equal(
        BigTiffFloatTileReader(exported.paths[1]).read_tile(0, 0), mask.astype(np.float32)
    )
    np.testing.assert_array_equal(
        BigTiffFloatTileReader(exported.paths[2]).read_tile(0, 0), imagery.astype(np.float32)
    )
    assert progress[-1] == 1.0
