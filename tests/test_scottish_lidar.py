from types import SimpleNamespace
from unittest.mock import patch

import pytest

from blender_terrain.catalog import DatasetKind, load_bundled_catalog
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.errors import DownloadIntegrityError
from blender_terrain.providers.scottish_lidar import open_scottish_lidar_reader


@pytest.mark.parametrize("kind", [DatasetKind.DTM, DatasetKind.DSM])
def test_nh24_is_a_specific_unselectable_asset(kind):
    catalog = load_bundled_catalog()
    product = catalog.product(f"GB_SCT_SRSP_PHASE1_NH24_{kind.name}")
    assert product.capabilities.kind is kind
    assert not product.selectable
    assert product.coverage.match(BBoxWGS84(-4.87, 57.48, -4.869, 57.481)).value == "potential"
    assert product.coverage.match(BBoxWGS84(-3.19, 55.95, -3.18, 55.96)).value == "none"
    assert product.license.commercial_safe


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
