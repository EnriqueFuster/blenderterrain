from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from blender_terrain.catalog import (
    DatasetKind,
    LayerRequest,
    ProductSelection,
    SelectionMode,
    load_bundled_catalog,
)
from blender_terrain.core.roi import BBoxWGS84, RegionOfInterest
from blender_terrain.io.bigtiff_tiles import DEFAULT_FLOAT_NODATA, GeoReference, TileLayout
from blender_terrain.io.imagery_window import ImageryWindowReader
from blender_terrain.models import ProjectedBounds
from blender_terrain.providers.registry import imagery_processor
from blender_terrain.providers.switzerland.imagery import SwisstopoImageryAcquirer
from blender_terrain.providers.switzerland.swisstopo import (
    SWISSIMAGE_COLLECTION,
    SwisstopoAsset,
    SwisstopoItem,
)


def _item() -> SwisstopoItem:
    bounds = BBoxWGS84(8.5375, 47.3684, 8.5509, 47.3776)
    assets = tuple(
        SwisstopoAsset(
            f"swissimage-{gsd}.tif",
            f"https://data.geo.admin.ch/swissimage-{gsd}.tif",
            "image/tiff; application=geotiff; profile=cloud-optimized",
            gsd,
            2056,
            ("ab" if gsd == 0.1 else "cd") * 32,
        )
        for gsd in (0.1, 2.0)
    )
    return SwisstopoItem(
        "swissimage-dop10_2025_2683-1247",
        SWISSIMAGE_COLLECTION,
        "2025-01-01T00:00:00Z",
        bounds,
        RegionOfInterest.from_bbox(bounds),
        assets,
    )


def test_acquires_swissimage_rgb_window_and_reuses_cache(tmp_path: Path) -> None:
    product = load_bundled_catalog().product("CH_SWISSIMAGE_DOP10")
    selection = ProductSelection(
        product.provider_id,
        product.id,
        DatasetKind.IMAGERY,
        SelectionMode.MANUAL,
        True,
    )
    exact = ProjectedBounds(2_683_100.0, 1_247_100.0, 2_683_110.0, 1_247_110.0, 2056)
    reference = GeoReference(2056, exact.west, exact.north, 0.1, -0.1, 2056)
    source = SimpleNamespace(
        georeference=reference,
        layout=TileLayout(100, 100, 100, 100, None),
        nodata=DEFAULT_FLOAT_NODATA,
        read_window=lambda row, column, height, width: np.full(
            (height, width, 3), (25.0, 100.0, 210.0), np.float32
        ),
        read_bounds=lambda bounds: (_ for _ in ()).throw(
            AssertionError("aligned acquisition must read the pixel window directly")
        ),
    )
    client = SimpleNamespace(search=lambda *args: (_item(),))
    acquirer = SwisstopoImageryAcquirer(
        catalog=load_bundled_catalog(),
        stac=client,
        reader_factory=lambda asset, target: source,
    )
    roi = BBoxWGS84(8.539, 47.369, 8.5391, 47.3691)

    with patch(
        "blender_terrain.providers.switzerland.imagery.project_swiss_roi",
        return_value=exact,
    ):
        acquired = acquirer.acquire(
            selection, LayerRequest(DatasetKind.IMAGERY, 1.0), roi, tmp_path
        )
        repeated = acquirer.acquire(
            selection, LayerRequest(DatasetKind.IMAGERY, 1.0), roi, tmp_path
        )

    assert len(acquired.paths) == 1
    assert repeated.cached_count == 1
    reader = ImageryWindowReader(acquired.paths[0])
    assert reader.metadata.bands == ("B04", "B03", "B02")
    assert reader.georeference.epsg == 2056
    assert reader.georeference.pixel_width == 1.0
    assert reader.data.shape == (10, 10, 3)
    assert np.all(reader.data == (25.0, 100.0, 210.0))
    assert acquired.auxiliary_paths[0].name == "source_manifest.json"


def test_swissimage_uses_the_shared_imagery_processor() -> None:
    assert imagery_processor("CH_SWISSIMAGE_DOP10") is not None


def test_splits_large_source_windows_without_increasing_memory_limit(
    tmp_path: Path, monkeypatch,
) -> None:
    product = load_bundled_catalog().product("CH_SWISSIMAGE_DOP10")
    selection = ProductSelection(
        product.provider_id, product.id, DatasetKind.IMAGERY, SelectionMode.MANUAL, True
    )
    exact = ProjectedBounds(2_683_100.0, 1_247_100.0, 2_683_110.0, 1_247_110.0, 2056)
    reference = GeoReference(2056, exact.west, exact.north, 1.0, -1.0, 2056)

    source = SimpleNamespace(
        georeference=reference,
        layout=TileLayout(10, 10, 10, 10, None),
        nodata=DEFAULT_FLOAT_NODATA,
        read_window=lambda row, column, height, width: np.ones(
            (height, width, 3), dtype=np.float32
        ),
        read_bounds=lambda bounds: (_ for _ in ()).throw(
            AssertionError("aligned acquisition must read the pixel window directly")
        ),
    )
    original = _item()
    asset = SwisstopoAsset(
        "swissimage-1.0.tif",
        original.assets[0].url,
        original.assets[0].media_type,
        1.0,
        2056,
        original.assets[0].sha256,
    )
    item = SwisstopoItem(
        original.id,
        original.collection,
        original.acquired_at,
        original.bounds,
        original.geometry,
        (asset,),
    )
    monkeypatch.setattr(
        "blender_terrain.providers.switzerland.imagery._MAXIMUM_WINDOW_PIXELS", 16
    )
    acquirer = SwisstopoImageryAcquirer(
        stac=SimpleNamespace(search=lambda *args: (item,)),
        reader_factory=lambda asset, target: source,
    )

    with patch(
        "blender_terrain.providers.switzerland.imagery.project_swiss_roi",
        return_value=exact,
    ):
        acquired = acquirer.acquire(
            selection,
            LayerRequest(DatasetKind.IMAGERY, 1.0),
            BBoxWGS84(8.539, 47.369, 8.5391, 47.3691),
            tmp_path,
        )

    assert len(acquired.paths) == 9
    assert all(
        max(ImageryWindowReader(path).data.shape[:2]) <= 4 for path in acquired.paths
    )
