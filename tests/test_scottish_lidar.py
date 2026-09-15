from types import SimpleNamespace
from unittest.mock import patch

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
from blender_terrain.errors import DownloadIntegrityError, NoCoverageError
from blender_terrain.io.elevation_window import ElevationWindowReader
from blender_terrain.providers.registry import build_raster_acquirers
from blender_terrain.providers.scottish_lidar import (
    ScottishLidarAcquirer,
    open_scottish_lidar_reader,
)


@pytest.mark.parametrize("kind", [DatasetKind.DTM, DatasetKind.DSM])
def test_nh24_is_a_specific_unselectable_asset(kind):
    catalog = load_bundled_catalog()
    product = catalog.product(f"GB_SCT_SRSP_PHASE1_NH24_{kind.name}")
    assert product.capabilities.kind is kind
    assert not product.selectable
    assert product.coverage.match(BBoxWGS84(-4.87, 57.48, -4.869, 57.481)).value == "potential"
    assert product.coverage.match(BBoxWGS84(-3.19, 55.95, -3.18, 55.96)).value == "none"
    assert product.license.commercial_safe


def test_researched_scottish_source_is_not_registered_for_jobs():
    assert build_raster_acquirers(["scottish_remote_sensing"]) == {}


def test_uses_range_reader_and_checks_asset_grid(tmp_path):
    product = load_bundled_catalog().product("GB_SCT_SRSP_PHASE1_NH24_DTM")
    reader = SimpleNamespace(
        georeference=SimpleNamespace(epsg=27700, pixel_width=1.0000025, pixel_height=-1.0000036),
        nodata=-9999.0,
    )
    with (
        patch("blender_terrain.providers.scottish_lidar.HttpRangeReader") as source,
        patch(
            "blender_terrain.providers.scottish_lidar.open_float_tile_reader", return_value=reader
        ),
    ):
        assert open_scottish_lidar_reader(product, tmp_path) is reader
        assert source.call_args.args == (product.endpoint, tmp_path)
        assert source.call_args.kwargs["allowed_hosts"] == frozenset(
            {"srsp-open-data.s3.eu-west-2.amazonaws.com"}
        )


def test_rejects_changed_asset_grid(tmp_path):
    product = load_bundled_catalog().product("GB_SCT_SRSP_PHASE1_NH24_DSM")
    reader = SimpleNamespace(
        georeference=SimpleNamespace(epsg=4326, pixel_width=1.0, pixel_height=-1.0),
        nodata=-9999.0,
    )
    with (
        patch("blender_terrain.providers.scottish_lidar.HttpRangeReader"),
        patch(
            "blender_terrain.providers.scottish_lidar.open_float_tile_reader", return_value=reader
        ),
        pytest.raises(DownloadIntegrityError, match="changed"),
    ):
        open_scottish_lidar_reader(product, tmp_path)


def test_nh24_acquisition_reads_bounded_window_and_reuses_cache(tmp_path):
    catalog = load_bundled_catalog()
    product = catalog.product("GB_SCT_SRSP_PHASE1_NH24_DTM")
    selection = ProductSelection(
        product.provider_id, product.id, DatasetKind.DTM, SelectionMode.MANUAL, True
    )
    source = SimpleNamespace(
        georeference=SimpleNamespace(
            origin_x=0.0, origin_y=100.0, pixel_width=1.0, pixel_height=-1.0
        ),
        layout=SimpleNamespace(width=100, height=100),
        nodata=-9999.0,
        read_window=lambda row, col, h, w: np.full((h, w), 12.0, dtype=np.float32),
    )
    with (
        patch("blender_terrain.providers.scottish_lidar.BritishGridTransform") as transform,
        patch(
            "blender_terrain.providers.scottish_lidar.open_scottish_lidar_reader",
            return_value=source,
        ) as opened,
    ):
        transform.return_value.forward.side_effect = lambda x, y: (
            np.full(x.shape, 20.5),
            np.full(y.shape, 80.5),
        )
        acquirer = ScottishLidarAcquirer(catalog, tmp_path / "grid.tif")
        roi = BBoxWGS84(-4.87, 57.48, -4.8699, 57.4801)
        acquired = acquirer.acquire(selection, LayerRequest(DatasetKind.DTM), roi, tmp_path)
        reader = ElevationWindowReader(acquired.paths[0])
        assert reader.georeference.epsg == 4326
        assert np.all(np.load(acquired.paths[0]) == 12.0)
        repeated = acquirer.acquire(selection, LayerRequest(DatasetKind.DTM), roi, tmp_path)
        assert repeated.cached_count == len(repeated.paths)
        assert opened.call_count == 1


def test_scottish_acquirer_rejects_unverified_campaign_before_network(tmp_path):
    catalog = load_bundled_catalog()
    product = catalog.product("GB_SCT_SRSP_CAMPAIGN_DTM")
    selection = ProductSelection(
        product.provider_id, product.id, DatasetKind.DTM, SelectionMode.MANUAL, True
    )
    with (
        patch("blender_terrain.providers.scottish_lidar.open_scottish_lidar_reader") as opened,
        pytest.raises(ValueError, match="unverified"),
    ):
        ScottishLidarAcquirer(catalog, tmp_path / "grid.tif").acquire(
            selection,
            LayerRequest(DatasetKind.DTM),
            BBoxWGS84(-4.87, 57.48, -4.869, 57.481),
            tmp_path,
        )
    opened.assert_not_called()


def test_nh24_rejects_roi_outside_asset_before_network(tmp_path):
    catalog = load_bundled_catalog()
    product = catalog.product("GB_SCT_SRSP_PHASE1_NH24_DTM")
    selection = ProductSelection(
        product.provider_id, product.id, DatasetKind.DTM, SelectionMode.MANUAL, True
    )
    with (
        patch("blender_terrain.providers.scottish_lidar.open_scottish_lidar_reader") as opened,
        pytest.raises(NoCoverageError, match="intersect"),
    ):
        ScottishLidarAcquirer(catalog, tmp_path / "grid.tif").acquire(
            selection,
            LayerRequest(DatasetKind.DTM),
            BBoxWGS84(-3.19, 55.95, -3.18, 55.96),
            tmp_path,
        )
    opened.assert_not_called()


def test_nh24_all_nodata_fails_on_first_and_cached_attempt(tmp_path):
    catalog = load_bundled_catalog()
    product = catalog.product("GB_SCT_SRSP_PHASE1_NH24_DTM")
    selection = ProductSelection(
        product.provider_id, product.id, DatasetKind.DTM, SelectionMode.MANUAL, True
    )
    source = SimpleNamespace(
        georeference=SimpleNamespace(
            origin_x=0.0, origin_y=100.0, pixel_width=1.0, pixel_height=-1.0
        ),
        layout=SimpleNamespace(width=100, height=100),
        nodata=-9999.0,
        read_window=lambda row, col, h, w: np.full((h, w), -9999.0, dtype=np.float32),
    )
    with (
        patch("blender_terrain.providers.scottish_lidar.BritishGridTransform") as transform,
        patch(
            "blender_terrain.providers.scottish_lidar.open_scottish_lidar_reader",
            return_value=source,
        ) as opened,
    ):
        transform.return_value.forward.side_effect = lambda x, y: (
            np.full(x.shape, 20.5),
            np.full(y.shape, 80.5),
        )
        acquirer = ScottishLidarAcquirer(catalog, tmp_path / "grid.tif")
        roi = BBoxWGS84(-4.87, 57.48, -4.8699, 57.4801)
        for _ in range(2):
            with pytest.raises(NoCoverageError, match="no elevation cells"):
                acquirer.acquire(selection, LayerRequest(DatasetKind.DTM), roi, tmp_path)
        assert opened.call_count == 1
