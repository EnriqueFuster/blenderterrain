from __future__ import annotations

import json
from email.message import Message
from urllib.parse import parse_qs, urlsplit

import pytest

from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.errors import ProviderContractChanged
from blender_terrain.providers.switzerland.swisstopo import (
    SWISSALTI3D_COLLECTION,
    SwisstopoCatalogClient,
    parse_swisstopo_page,
    swisstopo_items_url,
)


def _item(item_id: str, *, gsd: float = 0.5) -> dict[str, object]:
    return {
        "type": "Feature",
        "id": item_id,
        "collection": SWISSALTI3D_COLLECTION,
        "bbox": [8.5375, 47.3684, 8.5509, 47.3776],
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[8.5375, 47.3686], [8.5507, 47.3684], [8.5509, 47.3775],
                             [8.5377, 47.3776], [8.5375, 47.3686]]],
        },
        "properties": {"datetime": "2019-01-01T00:00:00Z"},
        "assets": {
            "height.tif": {
                "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                "href": f"https://data.geo.admin.ch/ch.swisstopo.swissalti3d/{item_id}/height.tif",
                "gsd": gsd,
                "proj:epsg": 2056,
                "file:checksum": "1220" + "AB" * 32,
            },
            "points.xyz.zip": {
                "type": "application/x.ascii-xyz+zip",
                "href": "https://data.geo.admin.ch/ignored.xyz.zip",
                "gsd": gsd,
                "proj:epsg": 2056,
            },
        },
    }


def test_builds_bounded_swisstopo_items_url() -> None:
    url = swisstopo_items_url(
        SWISSALTI3D_COLLECTION,
        BBoxWGS84(8.53, 47.36, 8.55, 47.38),
    )

    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    assert parsed.hostname == "data.geo.admin.ch"
    assert parsed.path.endswith(f"/{SWISSALTI3D_COLLECTION}/items")
    assert query == {"bbox": ["8.53,47.36,8.55,47.38"], "limit": ["100"]}


def test_parses_swisstopo_item_assets_by_metadata() -> None:
    payload = json.dumps({"type": "FeatureCollection", "features": [_item("tile-1")],
                          "links": []}).encode()

    page = parse_swisstopo_page(payload, SWISSALTI3D_COLLECTION, frozenset({0.5, 2.0}))

    assert page.next_url is None
    assert len(page.items) == 1
    item = page.items[0]
    assert item.id == "tile-1"
    assert item.acquired_at == "2019-01-01T00:00:00Z"
    assert item.bounds == BBoxWGS84(8.5375, 47.3684, 8.5509, 47.3776)
    assert len(item.assets) == 1
    assert item.assets[0].gsd == 0.5
    assert item.assets[0].epsg == 2056
    assert item.assets[0].sha256 == "ab" * 32


def test_rejects_untrusted_swisstopo_asset_host() -> None:
    feature = _item("tile-1")
    feature["assets"]["height.tif"]["href"] = "https://example.com/height.tif"  # type: ignore[index]
    payload = json.dumps({"type": "FeatureCollection", "features": [feature],
                          "links": []}).encode()

    with pytest.raises(ProviderContractChanged, match="asset values"):
        parse_swisstopo_page(payload, SWISSALTI3D_COLLECTION, frozenset({0.5, 2.0}))


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.headers = Message()

    def read(self, amount: int = -1) -> bytes:
        return self.payload[:amount]

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _Opener:
    def __init__(self, payloads: list[bytes]) -> None:
        self.payloads = payloads
        self.urls: list[str] = []

    def open(self, request, timeout: float):
        self.urls.append(request.full_url)
        return _Response(self.payloads.pop(0))


def test_catalog_client_follows_validated_next_link_and_deduplicates_items() -> None:
    next_url = (
        "https://data.geo.admin.ch/api/stac/v1/collections/"
        f"{SWISSALTI3D_COLLECTION}/items?cursor=opaque&limit=100"
    )
    first = json.dumps({"type": "FeatureCollection", "features": [_item("tile-1")],
                        "links": [{"rel": "next", "href": next_url}]}).encode()
    second = json.dumps({"type": "FeatureCollection",
                         "features": [_item("tile-1"), _item("tile-2", gsd=2.0)],
                         "links": []}).encode()
    opener = _Opener([first, second])
    client = SwisstopoCatalogClient(opener=opener)

    items = client.search(
        SWISSALTI3D_COLLECTION,
        BBoxWGS84(8.53, 47.36, 8.55, 47.38),
        frozenset({0.5, 2.0}),
    )

    assert [item.id for item in items] == ["tile-1", "tile-2"]
    assert opener.urls[1] == next_url


def test_rejects_untrusted_swisstopo_next_link() -> None:
    payload = json.dumps({"type": "FeatureCollection", "features": [],
                          "links": [{"rel": "next", "href":
                                     "https://example.com/items?cursor=opaque"}]}).encode()

    with pytest.raises(ProviderContractChanged, match="next link"):
        parse_swisstopo_page(payload, SWISSALTI3D_COLLECTION, frozenset({0.5}))
