from pathlib import Path

import numpy as np

from blender_terrain.core import BBoxWGS84, create_import_plan, geographic_source_bounds
from blender_terrain.core.imagery_processing import process_worldcover_imagery
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
