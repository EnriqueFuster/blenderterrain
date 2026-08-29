from pathlib import Path

import numpy as np
import pytest

from blender_terrain.core import BBoxWGS84, create_import_plan, geographic_source_bounds
from blender_terrain.core.bathymetry_processing import process_gebco_tiles
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
