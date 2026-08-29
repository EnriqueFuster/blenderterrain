from __future__ import annotations

from pathlib import Path

import numpy as np

from blender_terrain.catalog import DatasetKind, LayerRequest, ProductSelection, SelectionMode
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.io.bigtiff_tiles import GeoReference, TileLayout
from blender_terrain.io.imagery_window import ImageryWindowReader
from blender_terrain.models import ProjectedBounds
from blender_terrain.providers.worldcover import WorldCoverAcquirer


class Reader:
    def __init__(self, bounds: ProjectedBounds) -> None:
        self.bounds = bounds
        self.layout = TileLayout(100, 100, 10, 10, 0.0)
        self.georeference = GeoReference(
            4326,
            bounds.west,
            bounds.north,
            (bounds.east - bounds.west) / 4,
            -(bounds.north - bounds.south) / 4,
            4326,
        )

    @property
    def nodata(self) -> float:
        return 0.0

    def read_bounds(self, bounds: ProjectedBounds):
        data = np.ones((4, 4, 4), dtype=np.float32)
        return data, self.bounds


def test_acquires_and_reuses_bounded_rgbnir_window(tmp_path: Path) -> None:
    roi = BBoxWGS84(2.34, 48.85, 2.36, 48.87)
    bounds = ProjectedBounds(2.34, 48.85, 2.36, 48.87, 4326)
    urls: list[str] = []

    def factory(url: str, cache: Path) -> Reader:
        urls.append(url)
        return Reader(bounds)

    acquirer = WorldCoverAcquirer(factory)
    selection = ProductSelection(
        "esa_worldcover",
        "ESA_WORLDCOVER_S2_2021",
        DatasetKind.IMAGERY,
        SelectionMode.MANUAL,
        True,
    )
    request = LayerRequest(DatasetKind.IMAGERY, 10.0, "2021")

    first = acquirer.acquire(selection, request, roi, tmp_path)
    second = acquirer.acquire(selection, request, roi, tmp_path)

    assert len(urls) == 1
    assert urls[0].endswith("/N48/ESA_WorldCover_10m_2021_v200_N48E002_S2RGBNIR.tif")
    assert first.cached_count == 0
    assert second.cached_count == 1
    window = ImageryWindowReader(first.paths[0])
    assert window.data.shape == (4, 4, 4)
    assert window.metadata.bands == ("B02", "B03", "B04", "B08")
