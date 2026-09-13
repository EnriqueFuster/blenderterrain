from types import SimpleNamespace
from unittest.mock import patch

import pytest

from blender_terrain.catalog import load_bundled_catalog
from blender_terrain.errors import DownloadIntegrityError
from blender_terrain.providers.datamap_wales import open_wales_reader


@pytest.mark.parametrize("kind", ["DTM", "DSM"])
def test_opens_only_range_backed_welsh_mosaic(tmp_path, kind):
    product = load_bundled_catalog().product(f"GB_WLS_DMW_LIDAR_1M_32F_{kind}")
    reader = SimpleNamespace(
        georeference=SimpleNamespace(epsg=27700, pixel_width=1.0, pixel_height=-1.0),
        nodata=-9999.0,
    )
    with (
        patch("blender_terrain.providers.datamap_wales.HttpRangeReader") as source,
        patch(
            "blender_terrain.providers.datamap_wales.open_float_tile_reader", return_value=reader
        ),
    ):
        assert open_wales_reader(product, tmp_path) is reader
        assert source.call_args.args == (product.endpoint, tmp_path)
        assert source.call_args.kwargs["allowed_hosts"] == frozenset(
            {"dmwproductionblob.blob.core.windows.net"}
        )


def test_rejects_changed_wales_grid(tmp_path):
    product = load_bundled_catalog().product("GB_WLS_DMW_LIDAR_1M_32F_DTM")
    reader = SimpleNamespace(
        georeference=SimpleNamespace(epsg=4326, pixel_width=1.0, pixel_height=-1.0),
        nodata=-9999.0,
    )
    with (
        patch("blender_terrain.providers.datamap_wales.HttpRangeReader"),
        patch(
            "blender_terrain.providers.datamap_wales.open_float_tile_reader", return_value=reader
        ),
        pytest.raises(DownloadIntegrityError, match="changed"),
    ):
        open_wales_reader(product, tmp_path)


def test_rejects_other_providers_before_network(tmp_path):
    product = load_bundled_catalog().product("GB_ENG_EA_LIDAR_COMPOSITE_1M_DTM")
    with pytest.raises(ValueError, match="DataMapWales"):
        open_wales_reader(product, tmp_path)
