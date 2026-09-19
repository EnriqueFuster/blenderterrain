from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from blender_terrain.catalog import DatasetKind, LayerRequest, ProductSelection, SelectionMode
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.errors import NoCoverageError, ProviderContractChanged
from blender_terrain.io.bigtiff_tiles import GeoReference, TileLayout
from blender_terrain.io.imagery_window import ImageryWindowReader
from blender_terrain.models import ProjectedBounds
from blender_terrain.providers.registry import build_raster_acquirers
from blender_terrain.providers.sentinel2 import (
    Sentinel2Acquirer,
    Sentinel2CatalogClient,
    Sentinel2Scene,
    parse_sentinel2_search,
    select_sentinel2_scenes,
    sentinel2_search_body,
)

HOST = "e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com"


def test_registry_builds_sentinel2_only_when_requested() -> None:
    adapters = build_raster_acquirers(("sentinel2",))

    assert set(adapters) == {"sentinel2"}
    assert isinstance(adapters["sentinel2"], Sentinel2Acquirer)


def _feature(scene_id: str, cloud: float, acquired: str) -> dict[str, object]:
    base = f"https://{HOST}/sentinel-2-c1-l2a/30/U/XC/{scene_id}"
    return {
        "type": "Feature",
        "id": scene_id,
        "bbox": [-1.56, 51.32, 0.08, 52.34],
        "properties": {
            "datetime": acquired,
            "eo:cloud_cover": cloud,
            "proj:epsg": 32630,
        },
        "assets": {
            "red": {"href": f"{base}/B04.tif"},
            "green": {"href": f"{base}/B03.tif"},
            "blue": {"href": f"{base}/B02.tif"},
            "scl": {"href": f"{base}/SCL.tif"},
        },
    }


def test_builds_bounded_l2a_search() -> None:
    payload = json.loads(
        sentinel2_search_body(
            BBoxWGS84(-0.15, 51.49, -0.14, 51.50),
            "2026-06-01T00:00:00Z",
            "2026-09-01T00:00:00Z",
            15.0,
        )
    )

    assert payload["collections"] == ["sentinel-2-c1-l2a"]
    assert payload["bbox"] == [-0.15, 51.49, -0.14, 51.5]
    assert payload["query"] == {"eo:cloud_cover": {"lte": 15.0}}
    assert payload["limit"] == 20


def test_parses_and_orders_scenes_by_cloud_cover() -> None:
    payload = json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                _feature("S2A_T30UXC_20260801T100000_L2A", 12.5, "2026-08-01T10:00:00Z"),
                _feature("S2A_T30UXC_20260729T111649_L2A", 1.25, "2026-07-29T11:16:49Z"),
            ],
        }
    ).encode()

    scenes = parse_sentinel2_search(payload)

    assert [scene.id for scene in scenes] == [
        "S2A_T30UXC_20260729T111649_L2A",
        "S2A_T30UXC_20260801T100000_L2A",
    ]
    assert scenes[0].cloud_cover_percent == 1.25
    assert scenes[0].red_url.endswith("/B04.tif")
    assert scenes[0].scl_url.endswith("/SCL.tif")


def test_rejects_untrusted_assets_and_oversized_responses() -> None:
    feature = _feature("S2A_T30UXC_20260729T111649_L2A", 1.0, "2026-07-29T11:16:49Z")
    assets = feature["assets"]
    assert isinstance(assets, dict)
    red = assets["red"]
    assert isinstance(red, dict)
    red["href"] = "https://example.com/B04.tif"
    with pytest.raises(ProviderContractChanged, match="unsupported"):
        parse_sentinel2_search(
            json.dumps({"type": "FeatureCollection", "features": [feature]}).encode()
        )

    class Response:
        def read(self, amount: int = -1) -> bytes:
            return b"x" * amount

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class Opener:
        def open(self, request, timeout: float):
            assert request.full_url.endswith("/v1/search")
            return Response()

    with pytest.raises(ProviderContractChanged, match="size limit"):
        Sentinel2CatalogClient(Opener()).search(
            BBoxWGS84(-0.15, 51.49, -0.14, 51.50),
            "2026-06-01T00:00:00Z",
            "2026-09-01T00:00:00Z",
            15.0,
        )


def test_acquires_rgb_and_resamples_scene_classification(tmp_path: Path) -> None:
    scene = Sentinel2Scene(
        "S2A_T30UXC_20260729T111649_L2A",
        "2026-07-29T11:16:49Z",
        1.0,
        32630,
        BBoxWGS84(-1.0, 51.0, 0.0, 52.0),
        f"https://{HOST}/scene/B04.tif",
        f"https://{HOST}/scene/B03.tif",
        f"https://{HOST}/scene/B02.tif",
        f"https://{HOST}/scene/SCL.tif",
    )

    class Catalog:
        def search(self, *args, **kwargs):
            return (scene,)

    exact = ProjectedBounds(600_000.0, 5_700_000.0, 600_040.0, 5_700_040.0, 32630)

    class Reader:
        layout = TileLayout(4, 4, 4, 4, 0.0)
        georeference = GeoReference(32630, 600_000.0, 5_700_040.0, 10.0, -10.0, 32630)
        nodata = 0.0

        def __init__(self, value: float, scl: bool = False) -> None:
            self.value = value
            self.scl = scl

        def read_bounds(self, bounds: ProjectedBounds):
            if self.scl:
                return np.array([[4.0, 9.0], [4.0, 4.0]], np.float32), exact
            return np.full((4, 4), self.value, np.float32), exact

    def reader(url: str, cache: Path):
        if url.endswith("SCL.tif"):
            return Reader(0.0, True)
        return Reader({"B04.tif": 0.3, "B03.tif": 0.2, "B02.tif": 0.1}[url[-7:]])

    acquirer = Sentinel2Acquirer(Catalog(), reader)
    policy = "2026-06-01T00:00:00Z/2026-09-01T00:00:00Z;cloud=20"
    selection = ProductSelection(
        "sentinel2", "SENTINEL2_L2A", DatasetKind.IMAGERY, SelectionMode.MANUAL, True, policy
    )
    result = acquirer.acquire(
        selection,
        LayerRequest(DatasetKind.IMAGERY, 10.0, policy),
        BBoxWGS84(-0.15, 51.49, -0.14, 51.50),
        tmp_path,
    )

    window = ImageryWindowReader(result.paths[0])
    assert window.metadata.bands == ("B02", "B03", "B04", "SCL")
    assert window.data.shape == (4, 4, 4)
    assert window.data[0, 0].tolist() == pytest.approx([0.1, 0.2, 0.3, 4.0])
    assert window.data[0, 3, 3] == 9.0


def test_selects_one_clear_scene_per_grid_tile_and_requires_full_coverage() -> None:
    def scene(scene_id: str, cloud: float, bounds: BBoxWGS84) -> Sentinel2Scene:
        base = f"https://{HOST}/{scene_id}"
        return Sentinel2Scene(
            scene_id,
            "2026-07-29T11:16:49Z",
            cloud,
            32630,
            bounds,
            f"{base}/B04.tif",
            f"{base}/B03.tif",
            f"{base}/B02.tif",
            f"{base}/SCL.tif",
        )

    left = scene("S2A_T30UXC_20260729T111649_L2A", 1.0, BBoxWGS84(0.0, 0.0, 1.0, 1.0))
    left_cloudy = scene(
        "S2A_T30UXC_20260730T111649_L2A", 12.0, BBoxWGS84(0.0, 0.0, 1.0, 1.0)
    )
    right = scene("S2A_T30UXD_20260729T111649_L2A", 2.0, BBoxWGS84(1.0, 0.0, 2.0, 1.0))
    roi = BBoxWGS84(0.25, 0.25, 1.75, 0.75)

    selected = select_sentinel2_scenes((left, right, left_cloudy), roi)

    assert selected == (left, right)
    with pytest.raises(NoCoverageError, match="complete ROI"):
        select_sentinel2_scenes((left,), roi)
