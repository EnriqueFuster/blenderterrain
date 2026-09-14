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
from blender_terrain.errors import DownloadIntegrityError
from blender_terrain.io.elevation_window import ElevationWindowReader
from blender_terrain.providers.datamap_wales import DataMapWalesAcquirer, open_wales_reader


def test_acquisition_publishes_geographic_windows_and_reuses_cache(tmp_path):
    catalog = load_bundled_catalog()
    product = catalog.product("GB_WLS_DMW_LIDAR_1M_32F_DTM")
    selection = ProductSelection(
        product.provider_id, product.id, DatasetKind.DTM, SelectionMode.MANUAL, True
    )
    roi = BBoxWGS84(-3.18, 51.48, -3.1799, 51.4801)
    source = SimpleNamespace(
        georeference=SimpleNamespace(
            origin_x=0.0, origin_y=100.0, pixel_width=1.0, pixel_height=-1.0
        ),
        layout=SimpleNamespace(width=100, height=100),
        nodata=-9999.0,
        read_window=lambda row, col, h, w: np.full((h, w), 7.0, dtype=np.float32),
    )
    with (
        patch("blender_terrain.providers.datamap_wales.BritishGridTransform") as transform,
        patch(
            "blender_terrain.providers.datamap_wales.open_wales_reader", return_value=source
        ) as opened,
    ):
        transform.return_value.forward.side_effect = lambda x, y: (
            np.full(x.shape, 20.5),
            np.full(y.shape, 80.5),
        )
        acquirer = DataMapWalesAcquirer(catalog, tmp_path / "grid.tif")
        request = LayerRequest(DatasetKind.DTM, 100.0)
        acquired = acquirer.acquire(selection, request, roi, tmp_path)
        reader = ElevationWindowReader(acquired.paths[0])
        assert reader.georeference.epsg == 4326
        data, _ = reader.read_bounds(
            reader.georeference.window_bounds(
                SimpleNamespace(
                    row=0, column=0, width=reader.layout.width, height=reader.layout.height
                )
            )
        )
        assert np.all(data == 7.0)
        repeated = acquirer.acquire(selection, request, roi, tmp_path)
        assert repeated.cached_count == len(repeated.paths)
        assert opened.call_count == 1


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
