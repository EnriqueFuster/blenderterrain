from __future__ import annotations

import struct
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pytest

from blender_terrain.catalog import load_bundled_catalog
from blender_terrain.core.grid import GridSpec
from blender_terrain.io.bigtiff_tiles import open_float_tile_reader
from blender_terrain.models import ProjectedBounds
from blender_terrain.providers.environment_agency import (
    EnvironmentAgencyWCSClient,
    plan_environment_agency_requests,
)


def test_plans_bounded_native_wcs_requests() -> None:
    grid = GridSpec(ProjectedBounds(530000, 176000, 535000, 181000, 27700), 1.0, 5000, 5000)

    requests = plan_environment_agency_requests(grid, 2048)

    assert len(requests) == 9
    assert (requests[0].width, requests[0].height) == (2048, 2048)
    assert requests[-1].bounds == ProjectedBounds(534096, 176000, 535000, 176904, 27700)
    assert (requests[-1].width, requests[-1].height) == (904, 904)


def test_builds_verified_environment_agency_wcs_query() -> None:
    product = load_bundled_catalog().product("GB_ENG_EA_LIDAR_COMPOSITE_1M_DTM")
    request = plan_environment_agency_requests(
        GridSpec(ProjectedBounds(530000, 180000, 530100, 180100, 27700), 1.0, 100, 100),
        2048,
    )[0]

    url = EnvironmentAgencyWCSClient(product).request_url(request)
    query = parse_qs(urlsplit(url).query)

    assert product.wcs is not None
    assert query["service"] == ["WCS"]
    assert query["version"] == ["2.0.1"]
    assert query["request"] == ["GetCoverage"]
    assert query["coverageId"] == [product.wcs.coverage_id]
    assert query["subset"] == ["E(530000,530100)", "N(180000,180100)"]
    assert query["format"] == ["image/tiff;application=geotiff"]


@pytest.mark.parametrize(
    "grid",
    [
        GridSpec(ProjectedBounds(0, 0, 10, 10, 25830), 1.0, 10, 10),
        GridSpec(ProjectedBounds(0, 0, 10, 10, 27700), 2.0, 5, 5),
    ],
)
def test_rejects_non_native_or_non_bng_requests(grid: GridSpec) -> None:
    with pytest.raises(ValueError, match="1 m EPSG:27700"):
        plan_environment_agency_requests(grid, 2048)


def test_reads_observed_environment_agency_tiff_layout(tmp_path: Path) -> None:
    path = tmp_path / "ea.tif"
    expected = np.array([[1.25, 2.5], [3.75, 5.0]], dtype=">f4")
    _write_ea_layout(path, expected)

    reader = open_float_tile_reader(path)

    np.testing.assert_array_equal(reader.read_tile(0, 0), expected)
    assert reader.georeference.epsg == 27700
    assert reader.georeference.bounds(2, 2) == (530000.0, 180000.0, 530002.0, 180002.0)


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
