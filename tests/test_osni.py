import sys
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

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
from blender_terrain.errors import ProviderUnavailableError, RasterFormatError
from blender_terrain.io.elevation_window import ElevationWindowReader
from blender_terrain.models import ProjectedBounds
from blender_terrain.providers.osni import (
    OSNI_CRS_EPSG,
    OsniAcquirer,
    OsniGridCell,
    _resolve_resource_url,
    iter_osni_xyz,
    osni_archive_range,
    osni_archive_url,
    osni_member_name,
    parse_osni_grid,
    read_osni_sheet,
    select_osni_sheets,
)
from blender_terrain.providers.registry import build_raster_acquirers
from scripts.probe_osni_sample import main


def test_parses_sample_xyz_without_assigning_crs():
    lines = ["x y z\n", "344415 380625 88.5394\n", "344425 380625 88.6\n"]
    assert list(iter_osni_xyz(lines)) == [
        (344415.0, 380625.0, 88.5394),
        (344425.0, 380625.0, 88.6),
    ]


def test_parses_headerless_xyz_used_by_grouped_archives() -> None:
    assert list(iter_osni_xyz(["332005.0000 373605.0000 17.5639\n"])) == [
        (332005.0, 373605.0, 17.5639)
    ]


def test_official_irish_grid_is_explicit() -> None:
    assert OSNI_CRS_EPSG == 29903


def test_maps_coverage_quarters_to_grouped_sheet_archives() -> None:
    document = {
        "crs": {
            "type": "name",
            "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
        },
        "features": [
            {
                "properties": {"NAME": "230ne.tif"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[-7.52, 54.29], [-7.44, 54.29], [-7.44, 54.32], [-7.52, 54.32]]
                    ],
                },
            },
            {
                "properties": {"NAME": "230nw.tif"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[-7.60, 54.29], [-7.52, 54.29], [-7.52, 54.32], [-7.60, 54.32]]
                    ],
                },
            },
            {
                "properties": {"NAME": "231ne.tif"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[-7.44, 54.29], [-7.36, 54.29], [-7.36, 54.32], [-7.44, 54.32]]
                    ],
                },
            },
            {"properties": {"NAME": " "}, "geometry": {"type": "Polygon", "coordinates": []}},
        ],
    }
    cells = parse_osni_grid(document)
    assert select_osni_sheets(cells, -7.53, 54.30, -7.43, 54.31) == (230, 231)
    assert osni_archive_range(230) == (201, 250)
    assert "sheets_201-250.zip" in osni_archive_url(230)
    assert osni_member_name(230) == "Sheet230v4.txt"


@pytest.mark.parametrize("sheet", [0, 294])
def test_rejects_osni_sheet_outside_published_range(sheet: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 293"):
        osni_archive_range(sheet)


def test_decodes_a_complete_native_sheet_grid() -> None:
    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr(
            "Sheet230v4.txt",
            "x y z\n100 210 1\n110 210 2\n120 210 3\n100 200 4\n110 200 5\n120 200 6\n",
        )
    with ZipFile(payload) as archive:
        data, bounds = read_osni_sheet(archive, 230)
    np.testing.assert_array_equal(data, [[1, 2, 3], [4, 5, 6]])
    assert bounds == ProjectedBounds(95.0, 195.0, 125.0, 215.0, 29903)


def test_fills_positions_omitted_from_a_sparse_sheet_with_nodata() -> None:
    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr(
            "Sheet230v4.txt",
            "100 210 1\n120 210 3\n100 200 4\n120 200 6\n",
        )
    with ZipFile(payload) as archive:
        data, _bounds = read_osni_sheet(archive, 230)
    np.testing.assert_array_equal(data, [[1, -9999, 3], [4, -9999, 6]])


def test_acquirer_publishes_native_irish_grid_windows_and_reuses_cache(tmp_path: Path) -> None:
    archive_path = tmp_path / "source.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "Sheet230v4.txt",
            "x y z\n100 210 1\n110 210 2\n120 210 3\n100 200 4\n110 200 5\n120 200 6\n",
        )
    catalog = load_bundled_catalog()
    product = catalog.product("GB_NIR_OSNI_10M_DTM")
    selection = ProductSelection(
        product.provider_id, product.id, DatasetKind.DTM, SelectionMode.MANUAL, True
    )
    cell = OsniGridCell(230, -6.1, 54.5, -5.9, 54.7)
    opened = 0

    def open_archive(_url: str, _cache: Path) -> ZipFile:
        nonlocal opened
        opened += 1
        return ZipFile(archive_path)

    acquirer = OsniAcquirer(catalog, (cell,), open_archive)
    result = acquirer.acquire(
        selection,
        LayerRequest(DatasetKind.DTM, 10.0),
        BBoxWGS84(-6.05, 54.55, -6.0, 54.6),
        tmp_path / "cache",
    )
    reader = ElevationWindowReader(result.paths[0])
    assert reader.georeference.epsg == 29903
    assert reader.layout.width == 3
    assert reader.layout.height == 2
    repeated = acquirer.acquire(
        selection,
        LayerRequest(DatasetKind.DTM, 10.0),
        BBoxWGS84(-6.05, 54.55, -6.0, 54.6),
        tmp_path / "cache",
    )
    assert repeated.cached_count == 1
    assert opened == 1


class RedirectResponse(BytesIO):
    def __init__(self, url: str) -> None:
        super().__init__(b"P")
        self.url = url

    def geturl(self) -> str:
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class RedirectOpener:
    def __init__(self, url: str) -> None:
        self.url = url

    def open(self, request, timeout):
        assert request.get_header("Range") == "bytes=0-0"
        assert request.get_header("Referer") == "https://www.opendatani.gov.uk/"
        return RedirectResponse(self.url)


def test_accepts_only_signed_official_storage_redirects() -> None:
    resource = "https://admin.opendatani.gov.uk/dataset/id/resource/id/download/osni.zip"
    trusted = (
        "https://83025b28472d6aa2bf5ae59f3724aa78.eu.r2.cloudflarestorage.com/"
        "dx-ni-prod/osni.zip?X-Amz-Signature=test"
    )
    assert _resolve_resource_url(resource, RedirectOpener(trusted)) == trusted
    with pytest.raises(ProviderUnavailableError, match="untrusted"):
        _resolve_resource_url(resource, RedirectOpener("https://example.com/osni.zip?token=x"))


@pytest.mark.parametrize(
    "lines",
    [
        ["x y z", "344415 380625"],
        ["x y z", "344415 380625 NaN"],
        ["x y z", "344415 380625 wrong"],
    ],
)
def test_rejects_changed_or_invalid_sample(lines):
    with pytest.raises(RasterFormatError):
        list(iter_osni_xyz(lines))


def test_sample_zip_probe_reports_verified_grid(tmp_path, monkeypatch, capsys):
    sample = tmp_path / "official-format-synthetic.zip"
    with ZipFile(sample, "w") as archive:
        archive.writestr(
            "sample.txt",
            "x y z\n344415 380625 88.5394\n344425 380625 88.6\n344415 380635 88.7\n",
        )
    monkeypatch.setattr(sys, "argv", ["probe_osni_sample", str(sample)])
    main()
    output = capsys.readouterr().out
    assert "points=3" in output
    assert "minimum x step=10 m" in output
    assert "horizontal CRS=EPSG:29903" in output


def test_provider_registry_builds_osni_adapter() -> None:
    assert isinstance(build_raster_acquirers(("osni",))["osni"], OsniAcquirer)
