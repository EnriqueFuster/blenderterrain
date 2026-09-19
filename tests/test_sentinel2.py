from __future__ import annotations

import json

import pytest

from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.errors import ProviderContractChanged
from blender_terrain.providers.sentinel2 import (
    Sentinel2CatalogClient,
    parse_sentinel2_search,
    sentinel2_search_body,
)

HOST = "e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com"


def _feature(scene_id: str, cloud: float, acquired: str) -> dict[str, object]:
    base = f"https://{HOST}/sentinel-2-c1-l2a/30/U/XC/{scene_id}"
    return {
        "type": "Feature",
        "id": scene_id,
        "bbox": [-1.56, 51.32, 0.08, 52.34],
        "properties": {"datetime": acquired, "eo:cloud_cover": cloud},
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
                _feature("cloudy", 12.5, "2026-08-01T10:00:00Z"),
                _feature("clear", 1.25, "2026-07-29T11:16:49Z"),
            ],
        }
    ).encode()

    scenes = parse_sentinel2_search(payload)

    assert [scene.id for scene in scenes] == ["clear", "cloudy"]
    assert scenes[0].cloud_cover_percent == 1.25
    assert scenes[0].red_url.endswith("/B04.tif")
    assert scenes[0].scl_url.endswith("/SCL.tif")


def test_rejects_untrusted_assets_and_oversized_responses() -> None:
    feature = _feature("scene", 1.0, "2026-07-29T11:16:49Z")
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
