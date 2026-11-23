from __future__ import annotations

import json
from dataclasses import replace
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pytest

from blender_terrain.catalog import (
    DatasetKind,
    LayerRequest,
    ProductSelection,
    SelectionMode,
    load_bundled_catalog,
)
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.errors import ProviderContractChanged
from blender_terrain.io.elevation_window import ElevationWindowReader
from blender_terrain.models import ProjectedBounds
from blender_terrain.providers.registry import build_raster_acquirers, projected_work_areas
from blender_terrain.providers.switzerland import SwisstopoAcquirer
from blender_terrain.providers.switzerland.swisstopo import (
    SWISSALTI3D_COLLECTION,
    SwisstopoAsset,
    SwisstopoCatalogClient,
    SwisstopoElevationAcquirer,
    SwisstopoItem,
    parse_swisstopo_page,
    select_latest_swisstopo_items,
    select_swisstopo_asset,
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


def _asset(gsd: float) -> SwisstopoAsset:
    return SwisstopoAsset(
        f"height-{gsd}.tif",
        f"https://data.geo.admin.ch/height-{gsd}.tif",
        "image/tiff; application=geotiff; profile=cloud-optimized",
        gsd,
        2056,
        "ab" * 32,
    )


def _swiss_item() -> SwisstopoItem:
    bounds = BBoxWGS84(8.5375, 47.3684, 8.5509, 47.3776)
    from blender_terrain.core.roi import RegionOfInterest

    return SwisstopoItem(
        "swissalti3d_2019_2683-1247",
        SWISSALTI3D_COLLECTION,
        "2019-01-01T00:00:00Z",
        bounds,
        RegionOfInterest.from_bbox(bounds),
        (_asset(0.5), _asset(2.0)),
    )


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(None, 0.5), (0.5, 0.5), (1.0, 0.5), (2.0, 2.0), (5.0, 2.0)],
)
def test_selects_swisstopo_source_resolution_without_upsampling(
    requested: float | None, expected: float
) -> None:
    assert select_swisstopo_asset(_swiss_item(), requested).gsd == expected


def test_selects_latest_swisstopo_edition_for_the_same_footprint() -> None:
    old = _swiss_item()
    latest = replace(
        old,
        id="swissalti3d_2026_2683-1247",
        acquired_at="2026-01-01T00:00:00Z",
    )

    assert select_latest_swisstopo_items((old, latest)) == (latest,)


def test_swisstopo_elevation_is_registered_and_uses_lv95_work_area() -> None:
    product = load_bundled_catalog().product("CH_SWISSALTI3D")
    roi = BBoxWGS84(8.53, 47.36, 8.55, 47.38)

    assert isinstance(
        build_raster_acquirers(["swisstopo"])["swisstopo"], SwisstopoAcquirer
    )
    work_areas = projected_work_areas(product, roi)
    assert work_areas is not None
    assert len(work_areas) == 1
    assert work_areas[0].crs.epsg == 2056


def test_acquires_bounded_swisstopo_elevation_window_and_reuses_cache(tmp_path: Path) -> None:
    product = load_bundled_catalog().product("CH_SWISSALTI3D")
    selection = ProductSelection(
        product.provider_id, product.id, DatasetKind.DTM, SelectionMode.MANUAL, True
    )
    item = _swiss_item()
    catalog_client = SimpleNamespace(search=lambda *args: (item,))
    exact = ProjectedBounds(2_683_100.0, 1_247_100.0, 2_683_200.0, 1_247_200.0, 2056)
    source = SimpleNamespace(
        georeference=SimpleNamespace(
            epsg=2056,
            pixel_width=0.5,
            pixel_height=-0.5,
            bounds=lambda width, height: (2_683_000.0, 1_247_000.0,
                                          2_684_000.0, 1_248_000.0),
        ),
        layout=SimpleNamespace(width=2000, height=2000),
        nodata=-9999.0,
        read_bounds=lambda bounds: (np.full((200, 200), 512.5, np.float32), exact),
    )
    def reader_factory(asset, target):
        return source

    acquirer = SwisstopoElevationAcquirer(
        catalog=load_bundled_catalog(),
        stac=catalog_client,
        reader_factory=reader_factory,
    )
    roi = BBoxWGS84(8.539, 47.369, 8.540, 47.370)

    with patch(
        "blender_terrain.providers.switzerland.swisstopo.project_swiss_roi",
        return_value=exact,
    ):
        acquired = acquirer.acquire(
            selection, LayerRequest(DatasetKind.DTM, 0.5), roi, tmp_path
        )
        repeated = acquirer.acquire(
            selection, LayerRequest(DatasetKind.DTM, 0.5), roi, tmp_path
        )

    assert acquired.provider_id == "swisstopo"
    assert len(acquired.paths) == 1
    assert repeated.cached_count == 1
    reader = ElevationWindowReader(acquired.paths[0])
    assert reader.georeference.epsg == 2056
    assert reader.georeference.pixel_width == 0.5
    assert np.all(np.load(acquired.paths[0]) == 512.5)
    assert acquired.auxiliary_paths[0].name == "source_manifest.json"


def test_swisstopo_acquirer_rejects_imagery_selection_before_discovery(tmp_path: Path) -> None:
    client = SimpleNamespace(search=lambda *args: pytest.fail("unexpected STAC request"))
    acquirer = SwisstopoElevationAcquirer(stac=client)
    selection = ProductSelection(
        "swisstopo", "CH_SWISSIMAGE_DOP10", DatasetKind.IMAGERY,
        SelectionMode.MANUAL, True,
    )

    with pytest.raises(ValueError, match="elevation selection"):
        acquirer.acquire(
            selection,
            LayerRequest(DatasetKind.IMAGERY, 0.5),
            BBoxWGS84(8.53, 47.36, 8.55, 47.38),
            tmp_path,
        )
