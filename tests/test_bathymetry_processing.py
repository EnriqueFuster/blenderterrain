from pathlib import Path

import numpy as np
import pytest

from blender_terrain.core import (
    BBoxWGS84,
    RegionOfInterest,
    create_import_plan,
    geographic_source_bounds,
)
from blender_terrain.core.bathymetry_processing import (
    ProcessedBathymetryTile,
    compose_terrain_bathymetry,
    process_gebco_tiles,
)
from blender_terrain.core.elevation_processing import ProcessedElevationTile
from blender_terrain.core.grid import GridTile
from blender_terrain.core.planning import ImportPlan
from blender_terrain.errors import RasterFormatError
from blender_terrain.io.elevation_window import write_elevation_window
from blender_terrain.models import ProjectedBounds


def _sources(tmp_path: Path, tid_value: float = 11.0) -> tuple[Path, Path, ImportPlan]:
    plan = create_import_plan(
        BBoxWGS84(-1.0, 50.0, -0.998, 50.002),
        "COPERNICUS_GLO30_2021",
        30.0,
        False,
        None,
        native_resolution_override=30.0,
        use_global_utm=True,
    )
    source = geographic_source_bounds(plan)
    padding = 0.01
    bounds = ProjectedBounds(
        source.west - padding,
        source.south - padding,
        source.east + padding,
        source.north + padding,
        4326,
    )
    elevation_path = tmp_path / "bathymetry.npy"
    tid_path = tmp_path / "tid.npy"
    elevation = np.linspace(-100.0, -10.0, 64 * 64, dtype=np.float32).reshape(64, 64)
    tid = np.full((64, 64), tid_value, dtype=np.float32)
    write_elevation_window(elevation_path, elevation, bounds, -32768.0)
    write_elevation_window(tid_path, tid, bounds, 255.0)
    return elevation_path, tid_path, plan


def test_reprojects_bathymetry_and_tid_to_identical_terrain_grid(tmp_path: Path) -> None:
    elevation_path, tid_path, plan = _sources(tmp_path)

    outputs = process_gebco_tiles(elevation_path, tid_path, plan)

    assert len(outputs) == plan.terrain_tile_count
    for output in outputs:
        assert output.elevation.shape == output.tid.shape
        assert output.elevation.shape == (output.tile.rows + 1, output.tile.columns + 1)
        assert output.tid.dtype == np.uint8
        assert set(np.unique(output.tid)) == {11}
        assert np.all(output.elevation < 0.0)


def test_rejects_unknown_tid_codes_after_nearest_resampling(tmp_path: Path) -> None:
    elevation_path, tid_path, plan = _sources(tmp_path, 99.0)

    with pytest.raises(RasterFormatError, match="unknown quality codes"):
        process_gebco_tiles(elevation_path, tid_path, plan)


def test_bathymetry_preserves_the_polygon_roi_mask(tmp_path: Path) -> None:
    elevation_path, tid_path, plan = _sources(tmp_path)
    bounds = plan.bounds
    region = RegionOfInterest.from_geojson_geometry(
        {
            "type": "Polygon",
            "coordinates": [
                [
                    [bounds.west, bounds.south],
                    [bounds.east, bounds.south],
                    [bounds.west, bounds.north],
                    [bounds.west, bounds.south],
                ]
            ],
        }
    )

    outputs = process_gebco_tiles(elevation_path, tid_path, plan, region=region)

    assert any(np.any(tile.elevation == tile.nodata) for tile in outputs)
    assert all(
        np.array_equal(tile.elevation == tile.nodata, tile.tid == 255)
        for tile in outputs
    )


def test_composition_preserves_terrain_and_fills_every_bathymetry_gap() -> None:
    tile = GridTile(0, 0, 0, 0, 2, 2, ProjectedBounds(0, 0, 20, 20, 25830))
    terrain_data = np.asarray(
        ((-8.0, -9999.0, 5.0), (-6.0, -4.0, 7.0), (0.0, -2.0, -9999.0)),
        dtype=np.float32,
    )
    terrain = ProcessedElevationTile(0, tile, terrain_data, -9999.0, 0, 0, 0.0)
    bathymetry_data = np.full((3, 3), -100.0, dtype=np.float32)
    tid = np.asarray(((0, 11, 11), (0, 0, 11), (11, 0, 255)), dtype=np.uint8)
    bathymetry = ProcessedBathymetryTile(0, tile, bathymetry_data, tid, -32768.0)

    composed = compose_terrain_bathymetry((terrain,), (bathymetry,))[0]

    assert composed.elevation[0, 0] == -8.0  # Negative land remains terrain.
    assert composed.elevation[1, 1] == -4.0  # A small land island remains terrain.
    assert composed.elevation[0, 1] != -9999.0  # Coastal NoData becomes geometry.
    assert composed.elevation[2, 2] == -9999.0  # Unknown fallback remains NoData.
    assert composed.elevation[2, 0] < 0.0  # Near-zero source water becomes seabed.
    np.testing.assert_array_equal(
        composed.marine_mask,
        ((0, 1, 0), (0, 0, 0), (1, 0, 255)),
    )


def test_composition_stitches_adjacent_object_edges() -> None:
    left_tile = GridTile(0, 0, 0, 0, 1, 1, ProjectedBounds(0, 0, 10, 10, 25830))
    right_tile = GridTile(0, 1, 0, 1, 1, 1, ProjectedBounds(10, 0, 20, 10, 25830))
    left = ProcessedElevationTile(
        0,
        left_tile,
        np.asarray(((10.0, -9999.0), (10.0, -9999.0)), dtype=np.float32),
        -9999.0,
        0,
        0,
        0.0,
    )
    right = ProcessedElevationTile(
        0,
        right_tile,
        np.asarray(((-9999.0, -9999.0), (-9999.0, -9999.0)), dtype=np.float32),
        -9999.0,
        0,
        0,
        0.0,
    )
    left_bathymetry = ProcessedBathymetryTile(
        0, left_tile, np.full((2, 2), -10.0, np.float32), np.full((2, 2), 11, np.uint8), -32768.0
    )
    right_bathymetry = ProcessedBathymetryTile(
        0, right_tile, np.full((2, 2), -20.0, np.float32), np.full((2, 2), 11, np.uint8), -32768.0
    )

    composed = compose_terrain_bathymetry(
        (left, right), (left_bathymetry, right_bathymetry)
    )

    np.testing.assert_array_equal(composed[0].elevation[:, -1], composed[1].elevation[:, 0])
    assert np.all(composed[0].elevation != -9999.0)
    assert np.all(composed[1].elevation != -9999.0)
