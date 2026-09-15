import sys
from zipfile import ZipFile

import pytest

from blender_terrain.errors import RasterFormatError
from blender_terrain.providers.osni import iter_osni_xyz
from scripts.probe_osni_sample import main


def test_parses_sample_xyz_without_assigning_crs():
    lines = ["x y z\n", "344415 380625 88.5394\n", "344425 380625 88.6\n"]
    assert list(iter_osni_xyz(lines)) == [
        (344415.0, 380625.0, 88.5394),
        (344425.0, 380625.0, 88.6),
    ]


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
