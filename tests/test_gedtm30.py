from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from blender_terrain.catalog import DatasetKind, LayerRequest, ProductSelection, SelectionMode
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.io.bigtiff_tiles import GeoReference, PixelWindow, TileLayout
from blender_terrain.io.elevation_window import ElevationWindowReader
from blender_terrain.models import ProjectedBounds
from blender_terrain.providers.gedtm30 import Gedtm30Acquirer


class Reader:
    def __init__(self, data: np.ndarray, bounds: ProjectedBounds, nodata: float) -> None:
        self.data = data
        self.bounds = bounds
        self.nodata = nodata
        self.layout = TileLayout(data.shape[1], data.shape[0], 2, 2, nodata)
        self.georeference = GeoReference(
            bounds.epsg,
            bounds.west,
            bounds.north,
            (bounds.east - bounds.west) / data.shape[1],
            -(bounds.north - bounds.south) / data.shape[0],
            bounds.epsg,
        )

    def window_for_bounds(self, bounds: ProjectedBounds) -> PixelWindow:
        return self.georeference.enclosing_window(bounds)

    def read_bounds(self, bounds: ProjectedBounds):
        return self.data.copy(), self.bounds

    def read_window(self, row: int, column: int, height: int, width: int):
        return self.data[row : row + height, column : column + width].copy()


def test_acquires_and_reuses_elevation_with_uncertainty(tmp_path: Path) -> None:
    roi = BBoxWGS84(2.34, 48.85, 2.36, 48.87)
    bounds = ProjectedBounds(2.34, 48.85, 2.36, 48.87, 4326)
    elevation = np.arange(16, dtype=np.float32).reshape(4, 4)
    uncertainty = np.array(
        [[0.0, 1.0, 2.0, 3.0], [1.0, 2.0, 3.0, 4.0]] * 2,
        dtype=np.float32,
    )
    calls: list[str] = []

    def factory(url: str, cache: Path):
        calls.append(url)
        return Reader(
            uncertainty if "_std_" in url else elevation,
            bounds,
            0.0 if "_std_" in url else -9999.0,
        )

    acquirer = Gedtm30Acquirer(factory)
    selection = ProductSelection(
        "openlandmap", "GEDTM30_V11", DatasetKind.DTM, SelectionMode.MANUAL, True
    )
    request = LayerRequest(DatasetKind.DTM, 30.0)

    first = acquirer.acquire(selection, request, roi, tmp_path)
    second = acquirer.acquire(selection, request, roi, tmp_path)

    assert len(calls) == 2
    assert first.cached_count == 0
    assert second.cached_count == 1
    np.testing.assert_array_equal(
        ElevationWindowReader(first.paths[0]).read_bounds(bounds)[0], elevation
    )
    statistics = json.loads(first.auxiliary_paths[1].read_text(encoding="utf-8"))
    assert statistics["valid_samples"] == 14
    assert statistics["p95_m"] > statistics["mean_m"]
