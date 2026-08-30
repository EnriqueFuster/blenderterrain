from pathlib import Path

import numpy as np
import pytest

from blender_terrain.core import BBoxWGS84, create_import_plan, geographic_source_bounds
from blender_terrain.core.imagery_processing import process_worldcover_imagery
from blender_terrain.errors import NoCoverageError
from blender_terrain.io.imagery_window import write_imagery_window
from blender_terrain.io.png_validation import validate_png
from blender_terrain.models import ProjectedBounds


def test_reprojects_rgbnir_window_to_planned_png(tmp_path: Path) -> None:
    roi = BBoxWGS84(2.34, 48.85, 2.341, 48.851)
    plan = create_import_plan(
        roi,
        "GEDTM30_V11",
        30.0,
        True,
        10.0,
        native_resolution_override=30.0,
        use_global_utm=True,
    )
    source_bounds = geographic_source_bounds(plan)
    padding = 0.001
    bounds = ProjectedBounds(
        source_bounds.west - padding,
        source_bounds.south - padding,
        source_bounds.east + padding,
        source_bounds.north + padding,
        4326,
    )
    data = np.empty((64, 64, 4), dtype=np.float32)
    data[:] = (0.05, 0.10, 0.20, 0.30)
    source = tmp_path / "rgbnir.npy"
    write_imagery_window(source, data, bounds, 0.0, ("B02", "B03", "B04", "B08"))

    outputs = process_worldcover_imagery((source,), plan, tmp_path / "output")

    assert outputs
    for output in outputs:
        validate_png(output.path, output.width, output.height)
        assert output.bounds.epsg == plan.work_areas[0].crs.epsg


def test_accepts_partial_worldcover_and_rejects_an_entirely_uncovered_grid(
    tmp_path: Path,
) -> None:
    roi = BBoxWGS84(2.34, 48.85, 2.342, 48.852)
    plan = create_import_plan(
        roi,
        "GEDTM30_V11",
        30.0,
        True,
        10.0,
        native_resolution_override=30.0,
        use_global_utm=True,
    )
    source_bounds = geographic_source_bounds(plan)
    partial_bounds = ProjectedBounds(
        source_bounds.west,
        source_bounds.south,
        (source_bounds.west + source_bounds.east) / 2.0,
        source_bounds.north,
        4326,
    )
    partial = tmp_path / "partial.npy"
    write_imagery_window(
        partial,
        np.full((32, 32, 4), 0.15, np.float32),
        partial_bounds,
        0.0,
        ("B02", "B03", "B04", "B08"),
    )

    outputs = process_worldcover_imagery((partial,), plan, tmp_path / "partial-output")
    assert outputs

    outside = tmp_path / "outside.npy"
    write_imagery_window(
        outside,
        np.full((8, 8, 4), 0.15, np.float32),
        ProjectedBounds(-20.0, -20.0, -19.0, -19.0, 4326),
        0.0,
        ("B02", "B03", "B04", "B08"),
    )
    with pytest.raises(NoCoverageError, match="no usable imagery"):
        process_worldcover_imagery((outside,), plan, tmp_path / "outside-output")
