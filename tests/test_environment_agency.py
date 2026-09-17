from __future__ import annotations

import struct
from pathlib import Path
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
from blender_terrain.core.delivery import TransferProgress
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.errors import ProviderContractChanged
from blender_terrain.io.bigtiff_tiles import open_float_tile_reader
from blender_terrain.io.http_download import DownloadedAsset
from blender_terrain.providers.environment_agency import (
    EA_SURVEY_SEARCH_URL,
    EnvironmentAgencyAerialDownload,
    EnvironmentAgencyAerialTile,
    EnvironmentAgencyRequest,
    EnvironmentAgencyWCSAcquirer,
    EnvironmentAgencyWCSClient,
    environment_agency_aerial_query_url,
    environment_agency_aerial_search_body,
    parse_environment_agency_aerial_index,
    parse_environment_agency_aerial_search,
    plan_environment_agency_requests,
)
from blender_terrain.providers.registry import build_raster_acquirers


def test_plans_bounded_native_wcs_requests() -> None:
    roi = BBoxWGS84(-0.16, 51.49, -0.08, 51.535)

    requests = plan_environment_agency_requests(roi, 2048)

    assert len(requests) == 9
    assert requests[0].bounds.north == roi.north
    assert requests[0].bounds.west == roi.west
    assert requests[-1].bounds.south == roi.south
    assert requests[-1].bounds.east == roi.east
    assert all(max(request.width, request.height) <= 2048 for request in requests)


def test_builds_verified_environment_agency_wcs_query() -> None:
    product = load_bundled_catalog().product("GB_ENG_EA_LIDAR_COMPOSITE_1M_DTM")
    request = plan_environment_agency_requests(BBoxWGS84(-0.13, 51.5, -0.129, 51.5005), 2048)[0]

    url = EnvironmentAgencyWCSClient(product).request_url(request)
    query = parse_qs(urlsplit(url).query)

    assert product.wcs is not None
    assert query["service"] == ["WCS"]
    assert query["version"] == ["2.0.1"]
    assert query["request"] == ["GetCoverage"]
    assert query["coverageId"] == [product.wcs.coverage_id]
    assert query["subsettingCrs"] == ["http://www.opengis.net/def/crs/EPSG/0/4326"]
    assert query["outputCrs"] == ["http://www.opengis.net/def/crs/EPSG/0/4326"]
    assert query["subset"] == ["Lat(51.5,51.5005)", "Long(-0.13,-0.129)"]
    assert query["format"] == ["image/tiff;application=geotiff"]


@pytest.mark.parametrize(("maximum_dimension", "resolution"), [(8, 1.0), (2048, 0.0)])
def test_rejects_invalid_request_limits(maximum_dimension: int, resolution: float) -> None:
    with pytest.raises(ValueError, match="limits"):
        plan_environment_agency_requests(
            BBoxWGS84(-0.13, 51.5, -0.129, 51.5005), maximum_dimension, resolution
        )


def test_reads_observed_environment_agency_tiff_layout(tmp_path: Path) -> None:
    path = tmp_path / "ea.tif"
    expected = np.array([[1.25, 2.5], [3.75, 5.0]], dtype=">f4")
    _write_ea_layout(path, expected)

    reader = open_float_tile_reader(path)

    np.testing.assert_array_equal(reader.read_tile(0, 0), expected)
    assert reader.georeference.epsg == 27700
    assert reader.georeference.bounds(2, 2) == (530000.0, 180000.0, 530002.0, 180002.0)


def test_acquires_only_the_confirmed_environment_agency_product(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = load_bundled_catalog().product("GB_ENG_EA_LIDAR_COMPOSITE_1M_DTM")
    selection = ProductSelection(
        product.provider_id, product.id, DatasetKind.DTM, SelectionMode.MANUAL, True
    )
    progress: list[TransferProgress] = []

    def download(
        self: EnvironmentAgencyWCSClient,
        request: EnvironmentAgencyRequest,
        cache_directory: Path,
        progress_callback=None,
        cancellation_requested=lambda: False,
    ) -> DownloadedAsset:
        cache_directory.mkdir(parents=True, exist_ok=True)
        path = cache_directory / f"{request.row}-{request.column}.tif"
        path.write_bytes(b"fixture")
        if progress_callback is not None:
            progress_callback(7, 7)
        return DownloadedAsset(path, 7, False)

    monkeypatch.setattr(EnvironmentAgencyWCSClient, "download", download)

    result = EnvironmentAgencyWCSAcquirer(load_bundled_catalog()).acquire(
        selection,
        LayerRequest(DatasetKind.DTM, target_resolution_m=5.0),
        BBoxWGS84(-0.13, 51.5, -0.129, 51.5005),
        tmp_path,
        progress.append,
    )

    assert result.provider_id == product.provider_id
    assert result.product_id == product.id
    assert len(result.paths) == 1
    assert progress[-1].filename == "WCS block 1/1"


def test_provider_registry_builds_environment_agency_adapter() -> None:
    adapters = build_raster_acquirers(("environment_agency",))

    assert isinstance(adapters["environment_agency"], EnvironmentAgencyWCSAcquirer)


def test_builds_bounded_environment_agency_aerial_index_query() -> None:
    url = environment_agency_aerial_query_url(BBoxWGS84(-0.15, 51.49, -0.10, 51.53))
    query = parse_qs(urlsplit(url).query)

    assert query["geometry"] == ["-0.15,51.49,-0.1,51.53"]
    assert query["inSR"] == ["4326"]
    assert query["returnGeometry"] == ["false"]
    assert query["where"] == ["type IN ('RGB','RGBN')"]


def test_parses_and_ranks_environment_agency_aerial_tiles() -> None:
    payload = b"""{"features":[
      {"attributes":{"filename":"old.ecw","polygon_id":"P1","os_ref":"TQ3280",
       "os_ref_5k":"TQ3075","year":2008,"resolution":0.4,"type":"RGB","bands":"3","latest":"No"}},
      {"attributes":{"filename":"latest.ecw","polygon_id":"P2","os_ref":"TQ3280",
       "os_ref_5k":"TQ3075","year":2024,"resolution":0.1,"type":"RGBN","bands":"4","latest":"Yes"}}
    ]}"""

    assert parse_environment_agency_aerial_index(payload) == (
        EnvironmentAgencyAerialTile(
            "latest.ecw", "P2", "TQ3280", "TQ3075", 2024, 0.1, "RGBN", 4, True
        ),
        EnvironmentAgencyAerialTile(
            "old.ecw", "P1", "TQ3280", "TQ3075", 2008, 0.4, "RGB", 3, False
        ),
    )


def test_rejects_truncated_environment_agency_aerial_query() -> None:
    with pytest.raises(ProviderContractChanged, match="exceeds 2000"):
        parse_environment_agency_aerial_index(b'{"features":[],"exceededTransferLimit":true}')


def test_builds_environment_agency_aerial_search_polygon() -> None:
    roi = BBoxWGS84(-0.13, 51.5, -0.129, 51.501)

    body = environment_agency_aerial_search_body(roi)

    assert EA_SURVEY_SEARCH_URL.endswith("/tiles/collections/survey/search")
    assert body == (
        b'{"type":"Polygon","coordinates":[[[-0.13,51.5],[-0.129,51.5],'
        b'[-0.129,51.501],[-0.13,51.501],[-0.13,51.5]]]}'
    )


def test_parses_environment_agency_aerial_download_choices() -> None:
    payload = b'''{"count":3,"results":[
      {"product":{"id":"lidar_composite_dtm"},"year":{"id":"2022"},
       "resolution":{"id":"1"},"tile":{"id":"TQ2575","label":"TQ27ne"},
       "uri":"https://environment.data.gov.uk/tiles/ignored"},
      {"product":{"id":"vertical_aerial_photography_tiles_rgb"},
       "year":{"id":"2008"},"resolution":{"id":"0.4"},
       "tile":{"id":"TQ2575","label":"TQ27ne"},
       "uri":"https://environment.data.gov.uk/tiles/collections/survey/aerial/2008/0.4/TQ2575"},
      {"product":{"id":"vertical_aerial_photography_tiles_rgb"},
       "year":{"id":"2024"},"resolution":{"id":"0.2"},
       "tile":{"id":"TQ3075","label":"TQ37nw"},
       "uri":"https://environment.data.gov.uk/tiles/collections/survey/aerial/2024/0.2/TQ3075"}
    ]}'''

    assert parse_environment_agency_aerial_search(payload) == (
        EnvironmentAgencyAerialDownload(
            2024,
            0.2,
            "TQ3075",
            "TQ37nw",
            "https://environment.data.gov.uk/tiles/collections/survey/aerial/2024/0.2/"
            "TQ3075?subscription-key=public",
        ),
        EnvironmentAgencyAerialDownload(
            2008,
            0.4,
            "TQ2575",
            "TQ27ne",
            "https://environment.data.gov.uk/tiles/collections/survey/aerial/2008/0.4/"
            "TQ2575?subscription-key=public",
        ),
    )


def _write_ea_layout(path: Path, values: np.ndarray) -> None:
    entry_count = 12
    external_offset = 8 + 2 + entry_count * 12 + 4
    transform = struct.pack(
        ">16d",
        1.0, 0.0, 0.0, 530000.0,
        0.0, -1.0, 0.0, 180002.0,
        0.0, 0.0, 0.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )
    geo_keys = struct.pack(
        ">16H", 1, 1, 2, 3, 1024, 0, 1, 1, 1025, 0, 1, 1, 3072, 0, 1, 27700
    )
    data_offset = external_offset + len(transform) + len(geo_keys)

    def short(tag: int, value: int) -> bytes:
        return struct.pack(">HHI", tag, 3, 1) + struct.pack(">H", value) + b"\0\0"

    def long(tag: int, value: int) -> bytes:
        return struct.pack(">HHII", tag, 4, 1, value)

    entries = [
        long(256, 2), long(257, 2), short(258, 32), short(259, 1), short(277, 1),
        long(322, 2), long(323, 2), long(324, data_offset), long(325, values.nbytes),
        short(339, 3), struct.pack(">HHII", 34264, 12, 16, external_offset),
        struct.pack(">HHII", 34735, 3, 16, external_offset + len(transform)),
    ]
    entries.sort(key=lambda entry: struct.unpack(">H", entry[:2])[0])
    path.write_bytes(
        b"MM" + struct.pack(">HIH", 42, 8, entry_count)
        + b"".join(entries) + struct.pack(">I", 0) + transform + geo_keys
        + values.tobytes()
    )
