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
    ScottishPhase1MosaicReader,
    open_scottish_lidar_reader,
    phase1_tile_ids_for_bng_bounds,
    phase1_tile_url,
)


@pytest.mark.parametrize("kind", [DatasetKind.DTM, DatasetKind.DSM])
def test_nh24_is_retained_as_specific_non_selectable_evidence(kind):
    catalog = load_bundled_catalog()
    product = catalog.product(f"GB_SCT_SRSP_PHASE1_NH24_{kind.name}")
    assert product.capabilities.kind is kind
    assert not product.selectable
    assert product.coverage.match(BBoxWGS84(-4.87, 57.48, -4.869, 57.481)).value == "potential"
    assert product.coverage.match(BBoxWGS84(-3.19, 55.95, -3.18, 55.96)).value == "none"
    assert product.license.commercial_safe


def test_scottish_source_is_registered_for_jobs():
    adapters = build_raster_acquirers(["scottish_remote_sensing"])
    assert isinstance(adapters["scottish_remote_sensing"], ScottishLidarAcquirer)


def test_resolves_phase1_grid_tiles_without_a_remote_catalog():
    assert phase1_tile_ids_for_bng_bounds(220_000, 840_000, 224_000, 844_000) == ("NH24",)
    assert phase1_tile_ids_for_bng_bounds(219_999, 839_999, 220_001, 840_001) == (
        "NH13",
        "NH23",
        "NH14",
        "NH24",
    )
    assert phase1_tile_ids_for_bng_bounds(220_000, 840_000, 230_000, 850_000) == ("NH24",)


def test_builds_only_validated_phase1_urls():
    assert phase1_tile_url(DatasetKind.DTM, "NH24").endswith(
        "/dtm/27700/gridded/NH24_1M_DTM_PHASE1.tif"
    )
    with pytest.raises(ValueError, match="identifier"):
        phase1_tile_url(DatasetKind.IMAGERY, "../../unsafe")


def test_rejects_bounds_outside_the_british_grid():
    with pytest.raises(NoCoverageError, match="outside"):
        phase1_tile_ids_for_bng_bounds(-1, 840_000, 1, 840_001)


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


@pytest.mark.parametrize("partial", [False, True])
def test_nh24_acquisition_reads_bounded_window_and_reuses_cache(tmp_path, partial):
    catalog = load_bundled_catalog()
    product = catalog.product("GB_SCT_SRSP_PHASE1_NH24_DTM")
    selection = ProductSelection(
        product.provider_id, product.id, DatasetKind.DTM, SelectionMode.MANUAL, True
    )

    def read_window(row, col, h, w):
        data = np.full((h, w), 12.0, dtype=np.float32)
        if partial:
            data[:, : w // 2] = -9999.0
        return data

    source = SimpleNamespace(
        georeference=SimpleNamespace(
            origin_x=0.0, origin_y=100.0, pixel_width=1.0, pixel_height=-1.0
        ),
        layout=SimpleNamespace(width=100, height=100),
        nodata=-9999.0,
        read_window=read_window,
    )
    with (
        patch("blender_terrain.providers.scottish_lidar.BritishGridTransform") as transform,
        patch(
            "blender_terrain.providers.scottish_lidar.open_scottish_lidar_reader",
            return_value=source,
        ) as opened,
    ):
        transform.return_value.forward.side_effect = lambda x, y: (
            np.broadcast_to(20.5 + np.arange(x.shape[1]), x.shape),
            np.full(y.shape, 80.5),
        )
        acquirer = ScottishLidarAcquirer(catalog, tmp_path / "grid.tif")
        roi = BBoxWGS84(-4.87, 57.48, -4.8699, 57.4801)
        acquired = acquirer.acquire(selection, LayerRequest(DatasetKind.DTM), roi, tmp_path)
        reader = ElevationWindowReader(acquired.paths[0])
        assert reader.georeference.epsg == 4326
        data = np.load(acquired.paths[0])
        assert np.any(data == 12.0)
        assert bool(np.any(data == -9999.0)) == partial
        repeated = acquirer.acquire(selection, LayerRequest(DatasetKind.DTM), roi, tmp_path)
        assert repeated.cached_count == len(repeated.paths)
        assert opened.call_count == 1


def test_scottish_acquirer_rejects_incompatible_selection_before_network(tmp_path):
    catalog = load_bundled_catalog()
    product = catalog.product("GB_SCT_SRSP_PHASE1_DTM")
    selection = ProductSelection(
        "invalid_provider", product.id, DatasetKind.DTM, SelectionMode.MANUAL, True
    )
    with (
        patch("blender_terrain.providers.scottish_lidar.open_scottish_lidar_reader") as opened,
        pytest.raises(ValueError, match="incompatible"),
    ):
        ScottishLidarAcquirer(catalog, tmp_path / "grid.tif").acquire(
            selection,
            LayerRequest(DatasetKind.DTM),
            BBoxWGS84(-4.87, 57.48, -4.869, 57.481),
            tmp_path,
        )
    opened.assert_not_called()


def test_phase1_mosaic_reads_available_tiles_and_ignores_missing_ones(tmp_path):
    product = load_bundled_catalog().product("GB_SCT_SRSP_PHASE1_DTM")
    available = SimpleNamespace(
        georeference=SimpleNamespace(
            epsg=27700,
            origin_x=220_000.0,
            origin_y=850_000.0,
            pixel_width=1.0,
            pixel_height=-1.0,
            bounds=lambda width, height: (220_000.0, 840_000.0, 230_000.0, 850_000.0),
        ),
        layout=SimpleNamespace(width=10_000, height=10_000),
        nodata=-9999.0,
        read_window=lambda row, col, h, w: np.full((h, w), 42.0, dtype=np.float32),
    )

    def open_tile(source, cache):
        if "NH24" not in source.endpoint:
            raise NoCoverageError("missing")
        return available

    with patch(
        "blender_terrain.providers.scottish_lidar.open_scottish_lidar_reader",
        side_effect=open_tile,
    ):
        mosaic = ScottishPhase1MosaicReader(product, tmp_path)
        data = mosaic.read_window(450_000, 219_995, 10, 10)
        assert np.all(data[:, :5] == -9999.0)
        assert np.all(data[:, 5:] == 42.0)


def test_phase1_product_uses_sparse_mosaic_in_acquisition(tmp_path):
    catalog = load_bundled_catalog()
    product = catalog.product("GB_SCT_SRSP_PHASE1_DTM")
    selection = ProductSelection(
        product.provider_id, product.id, DatasetKind.DTM, SelectionMode.MANUAL, True
    )
    source = SimpleNamespace(
        georeference=SimpleNamespace(
            origin_x=0.0, origin_y=100.0, pixel_width=1.0, pixel_height=-1.0
        ),
        layout=SimpleNamespace(width=100, height=100),
        nodata=-9999.0,
        read_window=lambda row, col, h, w: np.full((h, w), 18.0, dtype=np.float32),
    )
    with (
        patch("blender_terrain.providers.scottish_lidar.BritishGridTransform") as transform,
        patch(
            "blender_terrain.providers.scottish_lidar.ScottishPhase1MosaicReader",
            return_value=source,
        ) as mosaic,
    ):
        transform.return_value.forward.side_effect = lambda x, y: (
            np.broadcast_to(20.5 + np.arange(x.shape[1]), x.shape),
            np.full(y.shape, 80.5),
        )
        acquired = ScottishLidarAcquirer(catalog, tmp_path / "grid.tif").acquire(
            selection,
            LayerRequest(DatasetKind.DTM),
            BBoxWGS84(-4.87, 57.48, -4.8699, 57.4801),
            tmp_path,
        )
        assert np.any(np.load(acquired.paths[0]) == 18.0)
        mosaic.assert_called_once()


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
