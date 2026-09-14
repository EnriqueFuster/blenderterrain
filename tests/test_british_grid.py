import os
from pathlib import Path

import numpy as np
import pytest

from blender_terrain.errors import DownloadIntegrityError, NoCoverageError, ProviderUnavailableError
from blender_terrain.providers.british_grid import BritishGridTransform, bundled_ostn15_path


def test_bundled_grid_transforms_cardiff():
    transform = BritishGridTransform(bundled_ostn15_path())
    np.testing.assert_allclose(
        transform.forward(-3.18, 51.48),
        (318153.2406714825, 176331.91924008727),
        atol=0.001,
        rtol=0,
    )


def test_missing_grid_fails_without_fallback(tmp_path):
    with pytest.raises(ProviderUnavailableError, match="missing"):
        BritishGridTransform(tmp_path / "missing.tif")


def test_corrupt_grid_fails_without_fallback(tmp_path):
    grid = tmp_path / "corrupt.tif"
    grid.write_bytes(b"not an OSTN15 grid")
    with pytest.raises(DownloadIntegrityError, match="checksum"):
        BritishGridTransform(grid)


@pytest.fixture
def grid_path():
    value = os.environ.get("OSTN15_TEST_GRID")
    if not value:
        pytest.skip("Set OSTN15_TEST_GRID to a verified local OSTN15 GeoTIFF")
    return Path(value)


def test_matches_epsg_etrs89_operation(grid_path):
    pyproj = pytest.importorskip("pyproj")
    original = pyproj.datadir.get_data_dir()
    try:
        pyproj.datadir.append_data_dir(str(grid_path.parent.resolve()))
        oracle = pyproj.Transformer.from_crs(
            4258, 27700, always_xy=True, allow_ballpark=False, only_best=True
        )
        longitude = np.array([-3.18, -4.1, -3.9])
        latitude = np.array([51.48, 52.9, 53.1])
        transform = BritishGridTransform(grid_path)
        actual = transform.forward(longitude, latitude)
        expected = oracle.transform(longitude, latitude, errcheck=True)
        np.testing.assert_allclose(actual, expected, atol=0.001, rtol=0)
        np.testing.assert_allclose(
            transform.inverse(*actual), (longitude, latitude), atol=1e-10, rtol=0
        )
    finally:
        pyproj.datadir.set_data_dir(original)


def test_outside_grid_is_not_approximated(grid_path):
    with pytest.raises(NoCoverageError, match="outside"):
        BritishGridTransform(grid_path).forward(10.0, 40.0)
