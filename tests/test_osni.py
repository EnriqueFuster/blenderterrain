import sys
from io import BytesIO
from zipfile import ZipFile

import pytest

from blender_terrain.errors import ProviderUnavailableError, RasterFormatError
from blender_terrain.providers.osni import (
    OSNI_CRS_EPSG,
    _resolve_resource_url,
    iter_osni_xyz,
    osni_archive_range,
    osni_archive_url,
    osni_member_name,
    parse_osni_grid,
    select_osni_sheets,
)
from scripts.probe_osni_sample import main


def test_parses_sample_xyz_without_assigning_crs():
    lines = ["x y z\n", "344415 380625 88.5394\n", "344425 380625 88.6\n"]
    assert list(iter_osni_xyz(lines)) == [
        (344415.0, 380625.0, 88.5394),
        (344425.0, 380625.0, 88.6),
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
        ["344415 380625 88.5394"],
        ["x y z", "344415 380625"],
        ["x y z", "344415 380625 NaN"],
        ["x y z", "344415 380625 wrong"],
    ],
)
def test_rejects_changed_or_invalid_sample(lines):
    with pytest.raises(RasterFormatError):
        list(iter_osni_xyz(lines))


def test_sample_zip_probe_reports_grid_but_never_assigns_crs(tmp_path, monkeypatch, capsys):
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
    assert "horizontal CRS=UNVERIFIED" in output
