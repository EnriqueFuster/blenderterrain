from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from blender_terrain.errors import RasterFormatError
from blender_terrain.io.elevation_window import (
    ElevationWindowReader,
    elevation_window_is_valid,
    write_elevation_window,
)
from blender_terrain.models import ProjectedBounds


def test_round_trips_a_georeferenced_float32_window(tmp_path: Path) -> None:
    path = tmp_path / "window.npy"
    data = np.arange(20, dtype=np.float32).reshape(4, 5)
    bounds = ProjectedBounds(2.0, 48.0, 2.5, 48.4, 4326)

    write_elevation_window(path, data, bounds, -9999.0)
    reader = ElevationWindowReader(path)
    actual, actual_bounds = reader.read_bounds(
        ProjectedBounds(2.1, 48.1, 2.4, 48.3, 4326)
    )

    assert elevation_window_is_valid(path)
    assert reader.georeference.pixel_width == pytest.approx(0.1)
    assert reader.georeference.pixel_height == pytest.approx(-0.1)
    assert actual_bounds.epsg == 4326
    assert (
        actual_bounds.west,
        actual_bounds.south,
        actual_bounds.east,
        actual_bounds.north,
    ) == pytest.approx((2.1, 48.1, 2.4, 48.3))
    np.testing.assert_array_equal(actual, data[1:3, 1:4])


def test_rejects_incomplete_or_overwritten_artifacts(tmp_path: Path) -> None:
    path = tmp_path / "window.npy"
    path.write_bytes(b"incomplete")

    assert not elevation_window_is_valid(path)
    with pytest.raises(RasterFormatError, match="overwrite"):
        write_elevation_window(
            path,
            np.ones((2, 2), dtype=np.float32),
            ProjectedBounds(0.0, 0.0, 1.0, 1.0, 4326),
            -9999.0,
        )
